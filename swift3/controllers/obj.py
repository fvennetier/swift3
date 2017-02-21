# Copyright (c) 2010-2014 OpenStack Foundation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from swift.common.http import HTTP_OK, HTTP_PARTIAL_CONTENT, HTTP_NO_CONTENT
from swift.common.swob import Range, content_range_header_value
from swift.common.utils import public

from swift3.utils import S3Timestamp, VERSIONING_SUFFIX, versioned_object_name
from swift3.controllers.base import Controller
from swift3.response import S3NotImplemented, InvalidRange, NoSuchKey, \
    InvalidArgument


class ObjectController(Controller):
    """
    Handles requests on objects
    """
    def _gen_head_range_resp(self, req_range, resp):
        """
        Swift doesn't handle Range header for HEAD requests.
        So, this method generates HEAD range response from HEAD response.
        S3 return HEAD range response, if the value of range satisfies the
        conditions which are described in the following document.
        - http://www.w3.org/Protocols/rfc2616/rfc2616-sec14.html#sec14.35
        """
        length = long(resp.headers.get('Content-Length'))

        try:
            content_range = Range(req_range)
        except ValueError:
            return resp

        ranges = content_range.ranges_for_length(length)
        if ranges == []:
            raise InvalidRange()
        elif ranges:
            if len(ranges) == 1:
                start, end = ranges[0]
                resp.headers['Content-Range'] = \
                    content_range_header_value(start, end, length)
                resp.headers['Content-Length'] = (end - start)
                resp.status = HTTP_PARTIAL_CONTENT
                return resp
            else:
                # TODO: It is necessary to confirm whether need to respond to
                #       multi-part response.(e.g. bytes=0-10,20-30)
                pass

        return resp

    def GETorHEAD(self, req):
        object_name = req.object_name
        version_id = req.params.get('versionId')
        if version_id and version_id != 'null':
            # get a specific version in the versioning container
            req.container_name += VERSIONING_SUFFIX
            req.object_name = versioned_object_name(
                req.object_name, req.params.pop('versionId'))

        try:
            resp = req.get_response(self.app)
        except NoSuchKey:
            resp = None
            if version_id and version_id != 'null':
                # if the specific version is not in the versioning container,
                # it might be the current version
                req.container_name = req.container_name[
                    :-len(VERSIONING_SUFFIX)]
                req.object_name = object_name
                info = req.get_object_info(self.app, object_name=object_name)
                if info.get('sysmeta', {}).get('version-id') == version_id:
                    resp = req.get_response(self.app)
            if resp is None:
                raise

        if req.method == 'HEAD':
            resp.app_iter = None

        if 'x-amz-meta-deleted' in resp.headers:
            raise NoSuchKey(object_name)

        for key in ('content-type', 'content-language', 'expires',
                    'cache-control', 'content-disposition',
                    'content-encoding'):
            if 'response-' + key in req.params:
                resp.headers[key] = req.params['response-' + key]

        return resp

    @public
    def HEAD(self, req):
        """
        Handle HEAD Object request
        """
        resp = self.GETorHEAD(req)

        if 'range' in req.headers:
            req_range = req.headers['range']
            resp = self._gen_head_range_resp(req_range, resp)

        return resp

    @public
    def GET(self, req):
        """
        Handle GET Object request
        """
        return self.GETorHEAD(req)

    @public
    def PUT(self, req):
        """
        Handle PUT Object and PUT Object (Copy) request
        """
        # set X-Timestamp by swift3 to use at copy resp body
        req_timestamp = S3Timestamp.now()
        req.headers['X-Timestamp'] = req_timestamp.internal
        if all(h in req.headers
               for h in ('X-Amz-Copy-Source', 'X-Amz-Copy-Source-Range')):
            raise InvalidArgument('x-amz-copy-source-range',
                                  req.headers['X-Amz-Copy-Source-Range'],
                                  'Illegal copy header')
        req.check_copy_source(self.app)
        resp = req.get_response(self.app)

        if 'X-Amz-Copy-Source' in req.headers:
            resp.append_copy_resp_body(req.controller_name,
                                       req_timestamp.s3xmlformat)

            # delete object metadata from response
            for key in list(resp.headers.keys()):
                if key.startswith('x-amz-meta-'):
                    del resp.headers[key]

        resp.status = HTTP_OK
        return resp

    @public
    def POST(self, req):
        raise S3NotImplemented()

    @public
    def DELETE(self, req):
        """
        Handle DELETE Object request
        """
        query = req.gen_multipart_manifest_delete_query(self.app)
        req.headers['Content-Type'] = None  # Ignore client content-type
        resp = req.get_response(self.app, query=query)
        if query and resp.status_int == HTTP_OK:
            for chunk in resp.app_iter:
                pass  # drain the bulk-deleter response
            resp.status = HTTP_NO_CONTENT
            resp.body = ''
        return resp
