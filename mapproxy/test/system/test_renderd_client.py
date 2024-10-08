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

import json

import pytest

from mapproxy.test.image import img_from_buf
from mapproxy.test.http import mock_single_req_httpd
from mapproxy.test.system import SysTest
from mapproxy.request.wms import (
    WMS111MapRequest,
    WMS111FeatureInfoRequest,
    WMS111CapabilitiesRequest,
)
from mapproxy.test.helper import validate_with_dtd
from mapproxy.test.http import mock_httpd
from mapproxy.test.image import create_tmp_image
from mapproxy.test.system.test_wms import is_111_exception
from mapproxy.cache.renderd import has_renderd_support


@pytest.fixture(scope="module")
def config_file():
    return "renderd_client.yaml"


pytestmark = pytest.mark.skipif(not has_renderd_support(), reason="requests required")

try:
    from http.server import BaseHTTPRequestHandler
except ImportError:
    from BaseHTTPServer import BaseHTTPRequestHandler


class TestWMS111(SysTest):

    def setup_method(self):
        self.common_req = WMS111MapRequest(
            url="/service?", param=dict(service="WMS", version="1.1.1")
        )
        self.common_map_req = WMS111MapRequest(
            url="/service?",
            param=dict(
                service="WMS",
                version="1.1.1",
                bbox="-180,0,0,80",
                width="200",
                height="200",
                layers="wms_cache",
                srs="EPSG:4326",
                format="image/png",
                exceptions="xml",
                styles="",
                request="GetMap",
            ),
        )
        self.common_fi_req = WMS111FeatureInfoRequest(
            url="/service?",
            param=dict(
                x="10",
                y="20",
                width="200",
                height="200",
                layers="wms_cache",
                format="image/png",
                query_layers="wms_cache",
                styles="",
                bbox="1000,400,2000,1400",
                srs="EPSG:900913",
            ),
        )

    def test_wms_capabilities(self, app):
        req = WMS111CapabilitiesRequest(url="/service?").copy_with_request_params(
            self.common_req
        )
        resp = app.get(req)
        assert resp.content_type == "application/vnd.ogc.wms_xml"
        xml = resp.lxml
        assert (
            xml.xpath(
                "//GetMap//OnlineResource/@xlink:href",
                namespaces=dict(xlink="http://www.w3.org/1999/xlink"),
            )[0]
            == "http://localhost/service?"
        )

        layer_names = set(xml.xpath("//Layer/Layer/Name/text()"))
        expected_names = set(["direct", "wms_cache", "tms_cache"])
        assert layer_names == expected_names
        assert validate_with_dtd(xml, dtd_name="wms/1.1.1/WMS_MS_Capabilities.dtd")

    def test_get_map(self, app, cache_dir):

        class req_handler(BaseHTTPRequestHandler):

            def do_POST(self):
                length = int(self.headers["content-length"])
                json_data = self.rfile.read(length)
                task = json.loads(json_data.decode("utf-8"))
                assert task["command"] == "tile"
                # request main tile of metatile
                assert task["tiles"] == [[15, 17, 5]]
                assert task["cache_identifier"] == "wms_cache_GLOBAL_MERCATOR"
                assert task["priority"] == 100
                # this id should not change for the same tile/cache_identifier combination
                assert task["id"] == "aeb52b506e4e82d0a1edf649d56e0451cfd5862c"

                # manually create tile renderd should create
                cache_dir.join(
                    "wms_cache_EPSG900913/05/000/000/016/000/000/016.jpeg"
                ).write_binary(
                    create_tmp_image((256, 256), format="jpeg", color=(255, 0, 100)),
                    ensure=True,
                )

                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')

            def log_request(self, code, size=None):
                pass

        with mock_single_req_httpd(("localhost", 42423), req_handler):
            self.common_map_req.params["bbox"] = "0,0,9,9"
            resp = app.get(self.common_map_req)

            img = img_from_buf(resp.body)
            main_color = sorted(img.convert("RGBA").getcolors())[-1]
            # check for red color (jpeg/png conversion requires fuzzy comparision)
            assert main_color[0] == 40000
            assert main_color[1][0] > 250
            assert main_color[1][1] < 5
            assert 95 < main_color[1][2] < 105
            assert main_color[1][3] == 255

            assert resp.content_type == "image/png"
        assert cache_dir.join(
            "wms_cache_EPSG900913/05/000/000/016/000/000/016.jpeg"
        ).check()

    def test_get_map_error(self, app):

        class req_handler(BaseHTTPRequestHandler):

            def do_POST(self):
                length = int(self.headers["content-length"])
                json_data = self.rfile.read(length)
                task = json.loads(json_data.decode("utf-8"))
                assert task["command"] == "tile"
                # request main tile of metatile
                assert task["tiles"] == [[15, 17, 5]]
                assert task["cache_identifier"] == "wms_cache_GLOBAL_MERCATOR"
                assert task["priority"] == 100
                # this id should not change for the same tile/cache_identifier combination
                assert task["id"] == "aeb52b506e4e82d0a1edf649d56e0451cfd5862c"

                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "error", "error_message": "barf"}')

            def log_request(self, code, size=None):
                pass

        with mock_single_req_httpd(("localhost", 42423), req_handler):
            self.common_map_req.params["bbox"] = "0,0,9,9"
            resp = app.get(self.common_map_req, expect_errors=True)

            assert resp.content_type == "application/vnd.ogc.se_xml"
            is_111_exception(resp.lxml, re_msg="Error from renderd: barf")

    def test_get_map_connection_error(self, app):
        self.common_map_req.params["bbox"] = "0,0,9,9"
        resp = app.get(self.common_map_req, expect_errors=True)

        assert resp.content_type == "application/vnd.ogc.se_xml"
        is_111_exception(resp.lxml, re_msg="Error while communicating with renderd:")

    def test_get_map_non_json_response(self, app):

        class req_handler(BaseHTTPRequestHandler):

            def do_POST(self):
                length = int(self.headers["content-length"])
                json_data = self.rfile.read(length)
                json.loads(json_data.decode("utf-8"))

                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"invalid')

            def log_request(self, code, size=None):
                pass

        with mock_single_req_httpd(("localhost", 42423), req_handler):
            self.common_map_req.params["bbox"] = "0,0,9,9"
            resp = app.get(self.common_map_req, expect_errors=True)

        assert resp.content_type == "application/vnd.ogc.se_xml"
        is_111_exception(
            resp.lxml, re_msg="Error while communicating with renderd: invalid JSON"
        )

    def test_get_featureinfo(self, app):
        expected_req = (
            {
                "path": r"/service?LAYERs=foo,bar&SERVICE=WMS&FORMAT=image%2Fpng"
                "&REQUEST=GetFeatureInfo&HEIGHT=200&SRS=EPSG%3A900913"
                "&VERSION=1.1.1&BBOX=1000.0,400.0,2000.0,1400.0&styles="
                "&WIDTH=200&QUERY_LAYERS=foo,bar&X=10&Y=20&feature_count=100"
            },
            {"body": b"info", "headers": {"content-type": "text/plain"}},
        )
        with mock_httpd(("localhost", 42423), [expected_req]):
            self.common_fi_req.params["feature_count"] = 100
            resp = app.get(self.common_fi_req)
            assert resp.content_type == "text/plain"
            assert resp.body == b"info"


class TestTiles(SysTest):

    def test_get_tile(self, app, cache_dir):

        class req_handler(BaseHTTPRequestHandler):

            def do_POST(self):
                length = int(self.headers["content-length"])
                json_data = self.rfile.read(length)
                task = json.loads(json_data.decode("utf-8"))
                assert task["command"] == "tile"
                assert task["tiles"] == [[10, 20, 6]]
                assert task["cache_identifier"] == "tms_cache_GLOBAL_MERCATOR"
                assert task["priority"] == 100
                # this id should not change for the same tile/cache_identifier combination
                assert task["id"] == "cf35c1c927158e188d8fbe0db380c1772b536da9"

                # manually create tile renderd should create
                cache_dir.join(
                    "tms_cache_EPSG900913/06/000/000/010/000/000/020.png"
                ).write_binary(b"foobaz", ensure=True)

                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')

            def log_request(self, code, size=None):
                pass

        with mock_single_req_httpd(("localhost", 42423), req_handler):
            resp = app.get("/tiles/tms_cache/EPSG900913/6/10/20.png")

            assert resp.content_type == "image/png"
            assert resp.body == b"foobaz"
        assert cache_dir.join(
            "tms_cache_EPSG900913/06/000/000/010/000/000/020.png"
        ).check()

    def test_get_tile_error(self, app):

        class req_handler(BaseHTTPRequestHandler):

            def do_POST(self):
                length = int(self.headers["content-length"])
                json_data = self.rfile.read(length)
                task = json.loads(json_data.decode("utf-8"))
                assert task["command"] == "tile"
                assert task["tiles"] == [[10, 20, 7]]
                assert task["cache_identifier"] == "tms_cache_GLOBAL_MERCATOR"
                assert task["priority"] == 100
                # this id should not change for the same tile/cache_identifier combination
                assert task["id"] == "c24b8c3247afec34fd0a53e5d3706e977877ef47"

                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(
                    b'{"status": "error", "error_message": "you told me to fail"}'
                )

            def log_request(self, code, size=None):
                pass

        with mock_single_req_httpd(("localhost", 42423), req_handler):
            resp = app.get("/tiles/tms_cache/EPSG900913/7/10/20.png", status=500)
            assert resp.content_type == "text/plain"
            assert resp.body == b"Error from renderd: you told me to fail"
