# This file is part of the MapProxy project.
# Copyright (C) 2018 Omniscale <http://omniscale.de>
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

import pytest

from mapproxy.exception import RequestError
from mapproxy.grid.tile_grid import tile_grid
from mapproxy.image import BlankImageSource
from mapproxy.image.opts import ImageOptions
from mapproxy.layer import MapLayer, DefaultMapExtent
from mapproxy.request.base import Request
from mapproxy.request.tile import tile_request
from mapproxy.request.wms import wms_request
from mapproxy.service.kml import KMLServer, kml_request
from mapproxy.service.tile import TileServer
from mapproxy.service.wms import WMSLayer, WMSGroupLayer, WMSServer
from mapproxy.test.http import make_wsgi_env


class DummyLayer(MapLayer):
    transparent = True
    extent = DefaultMapExtent()
    has_legend = False
    queryable = False

    def __init__(self, name):
        MapLayer.__init__(self)
        self.name = name
        self.requested = False
        self.queried = False

    def get_map(self, query):
        self.requested = True

    def get_info(self, query):
        self.queried = True

    def map_layers_for_query(self, query):
        return [(self.name, self)]

    def info_layers_for_query(self, query):
        return [(self.name, self)]


MAP_REQ = "FORMAT=image%2Fpng&SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&STYLES=&SRS=EPSG%3A4326&BBOX=5,46,8,48&WIDTH=60&HEIGHT=40"  # noqa
FI_REQ = "FORMAT=image%2Fpng&SERVICE=WMS&VERSION=1.1.1&REQUEST=GetFeatureInfo&STYLES=&SRS=EPSG%3A4326&BBOX=5,46,8,48&WIDTH=60&HEIGHT=40&X=30&Y=20"  # noqa


class TestWMSAuth(object):
    def setup_method(self):
        layers = {}
        wms_layers = {}

        # create test layer tree
        # - unnamed root
        #     - layer1
        #       - layer1a
        #       - layer1b
        #     - layer2
        #       - layer2a
        #       - layer2b
        #         - layer2b1

        layers['layer1a'] = DummyLayer('layer1a')
        wms_layers['layer1a'] = WMSLayer('layer1a', None, [layers['layer1a']],
                                         info_layers=[layers['layer1a']])
        layers['layer1b'] = DummyLayer('layer1b')
        wms_layers['layer1b'] = WMSLayer('layer1b', None, [layers['layer1b']],
                                         info_layers=[layers['layer1b']])
        wms_layers['layer1'] = WMSGroupLayer('layer1', None, None,
                                             [wms_layers['layer1a'], wms_layers['layer1b']])

        layers['layer2a'] = DummyLayer('layer2a')
        wms_layers['layer2a'] = WMSLayer('layer2a', None, [layers['layer2a']],
                                         info_layers=[layers['layer2a']])
        layers['layer2b1'] = DummyLayer('layer2b1')
        wms_layers['layer2b1'] = WMSLayer('layer2b1', None, [layers['layer2b1']],
                                          info_layers=[layers['layer2b1']])
        layers['layer2b'] = DummyLayer('layer2b')
        wms_layers['layer2b'] = WMSGroupLayer('layer2b', None, layers['layer2b'],
                                              [wms_layers['layer2b1']])
        wms_layers['layer2'] = WMSGroupLayer('layer2', None, None,
                                             [wms_layers['layer2a'], wms_layers['layer2b']])

        root_layer = WMSGroupLayer(None, 'root layer', None, [wms_layers['layer1'],
                                                              wms_layers['layer2']])
        self.wms_layers = wms_layers
        self.layers = layers
        self.server = WMSServer(md={}, root_layer=root_layer, srs=['EPSG:4326'],
                                image_formats={'image/png': ImageOptions(format='image/png')})


# ###
# see mapproxy.test.system.test_auth for WMS GetCapabilities request tests
# ###

class TestWMSGetMapAuth(TestWMSAuth):
    def map_request(self, layers, auth):
        env = make_wsgi_env(MAP_REQ+'&layers=' + layers, extra_environ={'mapproxy.authorize': auth})
        req = Request(env)
        return wms_request(req)

    def test_allow_all(self):
        def auth(service, layers, **kw):
            assert layers == 'layer1a layer1b'.split()
            return {'authorized': 'full'}
        self.server.map(self.map_request('layer1', auth))
        assert self.layers['layer1a'].requested
        assert self.layers['layer1b'].requested

    def test_root_with_partial_sublayers(self):
        # filter out sublayer layer1b
        def auth(service, layers, **kw):
            assert layers == 'layer1a layer1b'.split()
            return {
                'authorized': 'partial',
                'layers': {
                    'layer1': {'map': True},
                    'layer1a': {'map': True},
                    'layer1b': {'map': False},
                }
            }
        self.server.map(self.map_request('layer1', auth))
        assert self.layers['layer1a'].requested
        assert not self.layers['layer1b'].requested

    def test_accept_sublayer(self):
        def auth(service, layers, **kw):
            assert layers == 'layer1a'.split()
            return {
                'authorized': 'partial',
                'layers': {
                    'layer1': {'map': True},
                    'layer1a': {'map': True},
                    'layer1b': {'map': False},
                }
            }
        self.server.map(self.map_request('layer1a', auth))
        assert self.layers['layer1a'].requested
        assert not self.layers['layer1b'].requested

    def test_accept_sublayer_w_root_denied(self):
        def auth(service, layers, **kw):
            assert layers == 'layer1a'.split()
            return {
                'authorized': 'partial',
                'layers': {
                    'layer1': {'map': False},
                    'layer1a': {'map': True},
                    'layer1b': {'map': False},
                }
            }
        self.server.map(self.map_request('layer1a', auth))
        assert self.layers['layer1a'].requested
        assert not self.layers['layer1b'].requested

    def test_deny_sublayer(self):
        def auth(service, layers, **kw):
            assert layers == 'layer1b'.split()
            return {
                'authorized': 'partial',
                'layers': {
                    'layer1': {'map': True},
                    'layer1a': {'map': True},
                    'layer1b': {'map': False},
                }
            }

        with pytest.raises(RequestError):
            self.server.map(self.map_request('layer1b', auth))

    def test_deny_group_layer_w_source(self):
        def auth(service, layers, **kw):
            assert layers == 'layer2b'.split()
            return {
                'authorized': 'partial',
                'layers': {
                    'layer2b': {'map': False},
                }
            }
        with pytest.raises(RequestError):
            self.server.map(self.map_request('layer2b', auth))

    def test_nested_layers_with_partial_sublayers(self):
        def auth(service, layers, **kw):
            assert layers == 'layer1a layer1b layer2a layer2b'.split()
            return {
                'authorized': 'partial',
                'layers': {
                    'layer1a': {'map': False},
                    # deny is the default
                    # 'layer1b': {'map': False},
                    'layer2a': {'map': True},
                    'layer2b': {'map': False},
                }
            }
        self.server.map(self.map_request('layer1,layer2', auth))
        assert self.layers['layer2a'].requested
        assert not self.layers['layer2b'].requested
        assert not self.layers['layer1a'].requested
        assert not self.layers['layer1b'].requested

    def test_unauthenticated(self):
        def auth(service, layers, **kw):
            assert layers == 'layer1b'.split()
            return {
                'authorized': 'unauthenticated',
            }
        try:
            self.server.map(self.map_request('layer1b', auth))
        except RequestError as ex:
            assert ex.status == 401, '%s != 401' % (ex.status, )
        else:
            assert False, 'expected RequestError'


class TestWMSGetFeatureInfoAuth(TestWMSAuth):
    def fi_request(self, layers, auth):
        env = make_wsgi_env(FI_REQ+'&layers=%s&query_layers=%s' % (layers, layers),
                            extra_environ={'mapproxy.authorize': auth})
        req = Request(env)
        return wms_request(req)

    def test_root_with_partial_sublayers(self):
        # filter out sublayer layer1b
        def auth(service, layers, **kw):
            assert layers == 'layer1a layer1b'.split()
            return {
                'authorized': 'partial',
                'layers': {
                    'layer1': {'featureinfo': True},
                    'layer1a': {'featureinfo': True},
                    'layer1b': {'featureinfo': False},
                }
            }
        self.server.featureinfo(self.fi_request('layer1', auth))
        assert self.layers['layer1a'].queried
        assert not self.layers['layer1b'].queried

    def test_accept_sublayer(self):
        def auth(service, layers, **kw):
            assert layers == 'layer1a'.split()
            return {
                'authorized': 'partial',
                'layers': {
                    'layer1': {'featureinfo': True},
                    'layer1a': {'featureinfo': True},
                    'layer1b': {'featureinfo': False},
                }
            }
        self.server.featureinfo(self.fi_request('layer1a', auth))
        assert self.layers['layer1a'].queried
        assert not self.layers['layer1b'].queried

    def test_accept_sublayer_w_root_denied(self):
        def auth(service, layers, **kw):
            assert layers == 'layer1a'.split()
            return {
                'authorized': 'partial',
                'layers': {
                    'layer1': {'featureinfo': False},
                    'layer1a': {'featureinfo': True},
                    'layer1b': {'featureinfo': False},
                }
            }
        self.server.featureinfo(self.fi_request('layer1a', auth))
        assert self.layers['layer1a'].queried
        assert not self.layers['layer1b'].queried

    def test_deny_sublayer(self):
        def auth(service, layers, **kw):
            assert layers == 'layer1b'.split()
            return {
                'authorized': 'partial',
                'layers': {
                    'layer1': {'featureinfo': True},
                    'layer1a': {'featureinfo': True},
                    'layer1b': {'featureinfo': False},
                }
            }
        with pytest.raises(RequestError):
            self.server.featureinfo(self.fi_request('layer1b', auth))

    def test_deny_group_layer_w_source(self):
        def auth(service, layers, **kw):
            assert layers == 'layer2b'.split()
            return {
                'authorized': 'partial',
                'layers': {
                    'layer2b': {'featureinfo': False},
                }
            }
        with pytest.raises(RequestError):
            self.server.featureinfo(self.fi_request('layer2b', auth))

    def test_nested_layers_with_partial_sublayers(self):
        def auth(service, layers, **kw):
            assert layers == 'layer1a layer1b layer2a layer2b'.split()
            return {
                'authorized': 'partial',
                'layers': {
                    'layer1a': {'featureinfo': False},
                    # deny is the default
                    # 'layer1b': {'featureinfo': False},
                    'layer2a': {'featureinfo': True},
                    'layer2b': {'featureinfo': False},
                }
            }
        self.server.featureinfo(self.fi_request('layer1,layer2', auth))
        assert self.layers['layer2a'].queried
        assert not self.layers['layer2b'].queried
        assert not self.layers['layer1a'].queried
        assert not self.layers['layer1b'].queried


class DummyTileLayer(object):
    def __init__(self, name):
        self.requested = False
        self.name = name
        self.grid = tile_grid(900913)

    def tile_bbox(self, request, use_profiles=False):
        # this dummy code does not handle profiles and different tile origins!
        return self.grid.tile_bbox(request.tile)

    def render(self, tile_request, use_profiles=None, coverage=None, decorate_img=None):
        self.requested = True
        resp = BlankImageSource((256, 256), image_opts=ImageOptions(format='image/png'))
        resp.timestamp = 0
        return resp


class TestTMSAuth(object):
    service = 'tms'

    def setup_method(self):
        self.layers = {}

        self.layers['layer1'] = DummyTileLayer('layer1')
        self.layers['layer2'] = DummyTileLayer('layer2')
        self.layers['layer3'] = DummyTileLayer('layer3')
        self.server = TileServer(self.layers, {})

    def tile_request(self, tile, auth):
        env = make_wsgi_env('', extra_environ={'mapproxy.authorize': auth,
                                               'PATH_INFO': '/tms/1.0.0/'+tile})
        req = Request(env)
        return tile_request(req)

    def test_deny_all(self):
        def auth(service, layers, **kw):
            assert service == self.service
            assert layers == 'layer1'.split()
            return {
                'authorized': 'none',
            }
        with pytest.raises(RequestError):
            self.server.map(self.tile_request('layer1/0/0/0.png', auth))

    def test_deny_layer(self):
        def auth(service, layers, **kw):
            assert service == self.service
            assert layers == 'layer1'.split()
            return {
                'authorized': 'partial',
                'layers': {
                    'layer1': {'tile': False},
                    'layer2': {'tile': True},
                }
            }
        with pytest.raises(RequestError):
            self.server.map(self.tile_request('layer1/0/0/0.png', auth))

    def test_allow_all(self):
        def auth(service, layers, **kw):
            assert service == self.service
            assert layers == 'layer1'.split()
            return {
                'authorized': 'full',
            }
        self.server.map(self.tile_request('layer1/0/0/0.png', auth))
        assert self.layers['layer1'].requested

    def test_allow_layer(self):
        def auth(service, layers, **kw):
            assert service == self.service
            assert layers == 'layer1'.split()
            return {
                'authorized': 'partial',
                'layers': {
                    'layer1': {'tile': True},
                    'layer2': {'tile': False},
                }
            }
        self.server.map(self.tile_request('layer1/0/0/0.png', auth))
        assert self.layers['layer1'].requested


class TestTileAuth(TestTMSAuth):
    def tile_request(self, tile, auth):
        env = make_wsgi_env('', extra_environ={'mapproxy.authorize': auth,
                                               'PATH_INFO': '/tiles/'+tile})
        req = Request(env)
        return tile_request(req)


class TestKMLAuth(TestTMSAuth):
    service = 'kml'

    def setup_method(self):
        TestTMSAuth.setup_method(self)
        self.server = KMLServer(self.layers, {})

    def tile_request(self, tile, auth):
        env = make_wsgi_env('', extra_environ={'mapproxy.authorize': auth,
                                               'PATH_INFO': '/kml/'+tile})
        req = Request(env)
        return kml_request(req)
