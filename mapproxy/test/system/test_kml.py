# This file is part of the MapProxy project.
# Copyright (C) 2010-2012 Omniscale <http://omniscale.de>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import hashlib

from io import BytesIO

import pytest

from mapproxy.util.bbox import bbox_equals
from mapproxy.util.times import format_httpdate
from mapproxy.test.image import is_jpeg, tmp_image
from mapproxy.test.http import mock_httpd
from mapproxy.test.helper import validate_with_xsd
from mapproxy.test.system import SysTest


@pytest.fixture(scope="module")
def config_file():
    return "kml_layer.yaml"


ns = {"kml": "http://www.opengis.net/kml/2.2"}


class TestKML(SysTest):

    def test_get_out_of_bounds_tile(self, app):
        for coord in [(0, 0, -1), (-1, 0, 0), (0, -1, 0), (4, 2, 1), (1, 3, 0)]:
            x, y, z = coord
            url = "/kml/wms_cache/%d/%d/%d.kml" % (z, x, y)
            resp = app.get(url, status=404)
            assert "outside the bounding box" in resp

    def test_invalid_layer(self, app):
        resp = app.get("/kml/inVAlid/0/0/0.png", status=404)
        assert resp.content_type == "text/plain"
        assert "unknown layer: inVAlid" in resp

    def test_invalid_format(self, app):
        resp = app.get("/kml/wms_cache/0/0/1.png", status=404)
        assert resp.content_type == "text/plain"
        assert "invalid format" in resp

    def test_get_tile_tile_source_error(self, app):
        resp = app.get("/kml/wms_cache/0/0/0.jpeg", status=500)
        assert resp.content_type == "text/plain"
        assert "No response from URL" in resp

    def _check_tile_resp(self, resp):
        assert resp.content_type == "image/jpeg"
        assert resp.content_length == len(resp.body)
        data = BytesIO(resp.body)
        assert is_jpeg(data)

    def _update_timestamp(self, base_config, cache_dir):
        timestamp = 1234567890.0
        size = 10214
        os.utime(
            cache_dir.join(
                "wms_cache_EPSG900913/01/000/000/000/000/000/001.jpeg"
            ).strpath,
            (timestamp, timestamp),
        )
        max_age = base_config.tiles.expires_hours * 60 * 60
        md5 = hashlib.new('md5', (str(timestamp) + str(size)).encode("ascii"), usedforsecurity=False)
        etag = md5.hexdigest()
        return etag, max_age

    def _check_cache_control_headers(self, resp, etag, max_age, timestamp=1234567890.0):
        assert resp.headers["ETag"] == etag
        if timestamp is None:
            assert "Last-modified" not in resp.headers
        else:
            assert resp.headers["Last-modified"] == format_httpdate(timestamp)
        assert resp.headers["Cache-control"] == "public, max-age=%d, s-maxage=%d" % (
            max_age,
            max_age,
        )

    def test_get_cached_tile(self, app, base_config, cache_dir, fixture_cache_data):
        etag, max_age = self._update_timestamp(base_config, cache_dir)
        resp = app.get("/kml/wms_cache/1/0/1.jpeg")
        self._check_cache_control_headers(resp, etag, max_age)
        self._check_tile_resp(resp)

    def test_if_none_match(self, app, base_config, cache_dir, fixture_cache_data):
        etag, max_age = self._update_timestamp(base_config, cache_dir)
        resp = app.get("/kml/wms_cache/1/0/1.jpeg", headers={"If-None-Match": etag})
        assert resp.status == "304 Not Modified"
        self._check_cache_control_headers(resp, etag, max_age)

        resp = app.get(
            "/kml/wms_cache/1/0/1.jpeg", headers={"If-None-Match": etag + "foo"}
        )
        self._check_cache_control_headers(resp, etag, max_age)
        assert resp.status == "200 OK"
        self._check_tile_resp(resp)

    def test_get_kml(self, app, base_config):
        resp = app.get("/kml/wms_cache/0/0/0.kml")
        xml = resp.lxml
        assert validate_with_xsd(xml, "kml/2.2.0/ogckml22.xsd")
        assert bbox_equals(
            self._bbox(xml.xpath("/kml:kml/kml:Document", namespaces=ns)[0]),
            (-180, -90, 180, 90),
        )
        assert bbox_equals(
            self._bbox(
                xml.xpath("/kml:kml/kml:Document/kml:GroundOverlay", namespaces=ns)[0]
            ),
            (-180, 0, 0, 90),
        )
        assert xml.xpath(
            "/kml:kml/kml:Document/kml:GroundOverlay/kml:Icon/kml:href/text()",
            namespaces=ns,
        ) == [
            "http://localhost/kml/wms_cache/EPSG900913/1/0/1.jpeg",
            "http://localhost/kml/wms_cache/EPSG900913/1/1/1.jpeg",
            "http://localhost/kml/wms_cache/EPSG900913/1/0/0.jpeg",
            "http://localhost/kml/wms_cache/EPSG900913/1/1/0.jpeg",
        ]
        assert xml.xpath(
            "/kml:kml/kml:Document/kml:NetworkLink/kml:Link/kml:href/text()",
            namespaces=ns,
        ) == [
            "http://localhost/kml/wms_cache/EPSG900913/1/0/1.kml",
            "http://localhost/kml/wms_cache/EPSG900913/1/1/1.kml",
            "http://localhost/kml/wms_cache/EPSG900913/1/0/0.kml",
            "http://localhost/kml/wms_cache/EPSG900913/1/1/0.kml",
        ]

        md5 = hashlib.new('md5', resp.body, usedforsecurity=False)
        etag = md5.hexdigest()
        max_age = base_config.tiles.expires_hours * 60 * 60
        self._check_cache_control_headers(resp, etag, max_age, None)

        resp = app.get("/kml/wms_cache/0/0/0.kml", headers={"If-None-Match": etag})
        assert resp.status == "304 Not Modified"

    def test_get_kml_init(self, app):
        resp = app.get("/kml/wms_cache")
        xml = resp.lxml
        assert validate_with_xsd(xml, "kml/2.2.0/ogckml22.xsd")
        assert xml.xpath(
            "/kml:kml/kml:Document/kml:GroundOverlay/kml:Icon/kml:href/text()",
            namespaces=ns,
        ) == [
            "http://localhost/kml/wms_cache/EPSG900913/1/0/1.jpeg",
            "http://localhost/kml/wms_cache/EPSG900913/1/1/1.jpeg",
            "http://localhost/kml/wms_cache/EPSG900913/1/0/0.jpeg",
            "http://localhost/kml/wms_cache/EPSG900913/1/1/0.jpeg",
        ]
        assert xml.xpath(
            "/kml:kml/kml:Document/kml:NetworkLink/kml:Link/kml:href/text()",
            namespaces=ns,
        ) == [
            "http://localhost/kml/wms_cache/EPSG900913/1/0/1.kml",
            "http://localhost/kml/wms_cache/EPSG900913/1/1/1.kml",
            "http://localhost/kml/wms_cache/EPSG900913/1/0/0.kml",
            "http://localhost/kml/wms_cache/EPSG900913/1/1/0.kml",
        ]

    def test_get_kml_nw(self, app):
        resp = app.get("/kml/wms_cache_nw/1/0/0.kml")
        xml = resp.lxml

        assert validate_with_xsd(xml, "kml/2.2.0/ogckml22.xsd")

        assert bbox_equals(
            self._bbox(xml.xpath("/kml:kml/kml:Document", namespaces=ns)[0]),
            (-180, -90, 0, 0),
        )
        assert bbox_equals(
            self._bbox(
                xml.xpath("/kml:kml/kml:Document/kml:GroundOverlay", namespaces=ns)[0]
            ),
            (-180, -66.51326, -90, 0),
        )

        assert xml.xpath(
            "/kml:kml/kml:Document/kml:GroundOverlay/kml:Icon/kml:href/text()",
            namespaces=ns,
        ) == [
            "http://localhost/kml/wms_cache_nw/EPSG900913/2/0/1.jpeg",
            "http://localhost/kml/wms_cache_nw/EPSG900913/2/1/1.jpeg",
            "http://localhost/kml/wms_cache_nw/EPSG900913/2/0/0.jpeg",
            "http://localhost/kml/wms_cache_nw/EPSG900913/2/1/0.jpeg",
        ]
        assert xml.xpath(
            "/kml:kml/kml:Document/kml:NetworkLink/kml:Link/kml:href/text()",
            namespaces=ns,
        ) == [
            "http://localhost/kml/wms_cache_nw/EPSG900913/2/0/1.kml",
            "http://localhost/kml/wms_cache_nw/EPSG900913/2/1/1.kml",
            "http://localhost/kml/wms_cache_nw/EPSG900913/2/0/0.kml",
            "http://localhost/kml/wms_cache_nw/EPSG900913/2/1/0.kml",
        ]

    def test_get_kml2(self, app):
        resp = app.get("/kml/wms_cache/1/0/1.kml")
        xml = resp.lxml
        assert validate_with_xsd(xml, "kml/2.2.0/ogckml22.xsd")

    def test_get_kml_multi_layer(self, app):
        resp = app.get("/kml/wms_cache_multi/1/0/0.kml")
        xml = resp.lxml
        assert validate_with_xsd(xml, "kml/2.2.0/ogckml22.xsd")
        assert xml.xpath(
            "/kml:kml/kml:Document/kml:GroundOverlay/kml:Icon/kml:href/text()",
            namespaces=ns,
        ) == [
            "http://localhost/kml/wms_cache_multi/EPSG4326/2/0/1.jpeg",
            "http://localhost/kml/wms_cache_multi/EPSG4326/2/1/1.jpeg",
            "http://localhost/kml/wms_cache_multi/EPSG4326/2/0/0.jpeg",
            "http://localhost/kml/wms_cache_multi/EPSG4326/2/1/0.jpeg",
        ]
        assert xml.xpath(
            "/kml:kml/kml:Document/kml:NetworkLink/kml:Link/kml:href/text()",
            namespaces=ns,
        ) == [
            "http://localhost/kml/wms_cache_multi/EPSG4326/2/0/1.kml",
            "http://localhost/kml/wms_cache_multi/EPSG4326/2/1/1.kml",
            "http://localhost/kml/wms_cache_multi/EPSG4326/2/0/0.kml",
            "http://localhost/kml/wms_cache_multi/EPSG4326/2/1/0.kml",
        ]

    def test_get_tile(self, app, cache_dir):
        with tmp_image((256, 256), format="jpeg") as img:
            expected_req = (
                {
                    "path": r"/service?LAYERs=foo,bar&SERVICE=WMS&FORMAT=image%2Fjpeg"
                    "&REQUEST=GetMap&HEIGHT=256&SRS=EPSG%3A900913&styles="
                    "&VERSION=1.1.1&BBOX=-20037508.3428,-20037508.3428,0.0,0.0"
                    "&WIDTH=256"
                },
                {"body": img.read(), "headers": {"content-type": "image/jpeg"}},
            )
            with mock_httpd(
                ("localhost", 42423), [expected_req], bbox_aware_query_comparator=True
            ):
                resp = app.get("/kml/wms_cache/1/0/0.jpeg")
                assert resp.content_type == "image/jpeg"
        assert cache_dir.join(
            "wms_cache_EPSG900913/01/000/000/000/000/000/000.jpeg"
        ).check()

    def _bbox(self, elem):
        elems = elem.xpath("kml:Region/kml:LatLonAltBox", namespaces=ns)[0]
        n, s, e, w = [float(elem.text) for elem in elems.getchildren()]
        return w, s, e, n
