# Copyright (c) 2014 OpenStack Foundation.
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

import lxml.etree
from copy import deepcopy
from pkg_resources import resource_stream  # pylint: disable-msg=E0611
from six.moves.urllib.parse import quote

from swift3.exception import S3Exception
from swift3.utils import LOGGER, camel_to_snake, utf8encode, utf8decode

XMLNS_S3 = 'http://s3.amazonaws.com/doc/2006-03-01/'
XMLNS_XSI = 'http://www.w3.org/2001/XMLSchema-instance'
URLENCODE_BLACKLIST = ['LastModified', 'ID', 'DisplayName', 'Initiated',
                       'ContinuationToken', 'NextContinuationToken', 'ETag']


class XMLSyntaxError(S3Exception):
    pass


class DocumentInvalid(S3Exception):
    pass


def cleanup_namespaces(elem):
    def remove_ns(tag, ns):
        if tag.startswith('{%s}' % ns):
            tag = tag[len('{%s}' % ns):]
        return tag

    if not isinstance(elem.tag, basestring):
        # elem is a comment element.
        return

    # remove s3 namespace
    elem.tag = remove_ns(elem.tag, XMLNS_S3)

    # remove default namespace
    if elem.nsmap and None in elem.nsmap:
        elem.tag = remove_ns(elem.tag, elem.nsmap[None])

    for e in elem.iterchildren():
        cleanup_namespaces(e)


def fromstring(text, root_tag=None):
    try:
        elem = lxml.etree.fromstring(text, parser)
    except lxml.etree.XMLSyntaxError as e:
        LOGGER.debug(e)
        raise XMLSyntaxError(e)

    cleanup_namespaces(elem)

    if root_tag is not None:
        # validate XML
        try:
            path = 'schema/%s.rng' % camel_to_snake(root_tag)
            with resource_stream(__name__, path) as rng:
                lxml.etree.RelaxNG(file=rng).assertValid(elem)
        except IOError as e:
            # Probably, the schema file doesn't exist.
            LOGGER.error(e)
            raise
        except lxml.etree.DocumentInvalid as e:
            LOGGER.debug(e)
            raise DocumentInvalid(e)

    return elem


def tostring(tree, encoding_type=None, use_s3ns=True):
    if use_s3ns:
        nsmap = tree.nsmap.copy()
        nsmap[None] = XMLNS_S3

        root = Element(tree.tag, attrib=tree.attrib, nsmap=nsmap)
        root.text = tree.text
        root.extend(deepcopy(tree.getchildren()))
        tree = root

    if encoding_type == 'url':
        tree = deepcopy(tree)
        for e in tree.iter():
            # Some elements are not url-encoded even when we specify
            # encoding_type=url.
            if e.tag not in URLENCODE_BLACKLIST:
                if isinstance(e.text, basestring):
                    # If the value contains control chars,
                    # it may be urlencoded already.
                    if e.get('urlencoded', None) == 'True':
                        e.attrib.pop('urlencoded')
                    else:
                        e.text = quote(e.text)

    return lxml.etree.tostring(tree, xml_declaration=True, encoding='UTF-8')


class _Element(lxml.etree.ElementBase):
    """
    Wrapper Element class of lxml.etree.Element to support
    a utf-8 encoded non-ascii string as a text.

    Why we need this?:
    Original lxml.etree.Element supports only unicode for the text.
    It declines maintainability because we have to call a lot of encode/decode
    methods to apply account/container/object name (i.e. PATH_INFO) to each
    Element instance. When using this class, we can remove such a redundant
    codes from swift3 middleware.
    """
    def __init__(self, *args, **kwargs):
        # pylint: disable-msg=E1002
        super(_Element, self).__init__(*args, **kwargs)

    def _init(self, *args, **kwargs):
        super(_Element, self)._init(*args, **kwargs)
        self.encoding_type = None

    @property
    def text(self):
        """
        utf-8 wrapper property of lxml.etree.Element.text
        """
        return utf8encode(lxml.etree.ElementBase.text.__get__(self))

    @text.setter
    def text(self, value):
        decoded = utf8decode(value)
        try:
            lxml.etree.ElementBase.text.__set__(self, decoded)
        except ValueError:
            root = self.getroottree().getroot()
            # URL encoding is usually done at the end, but sometimes we get
            # control characters that are rejected by the XML encoder.
            # If we are going to urlencode the value, do it now.
            if root.encoding_type != 'url' or \
                    self.tag in URLENCODE_BLACKLIST:
                raise
            lxml.etree.ElementBase.text.__set__(self, quote(decoded))
            # The deepcopy seems to not copy custom fields, thus we use
            # an attribute which will be deleted when marshalling.
            self.set('urlencoded', 'True')


parser_lookup = lxml.etree.ElementDefaultClassLookup(element=_Element)
parser = lxml.etree.XMLParser()
parser.set_element_class_lookup(parser_lookup)

Element = parser.makeelement
SubElement = lxml.etree.SubElement
