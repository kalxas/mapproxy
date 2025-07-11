# This file is part of the MapProxy project.
# Copyright (C) 2010-2016 Omniscale <http://omniscale.de>
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

"""
Configuration loading and system initializing.
"""
from __future__ import division
from mapproxy.util.fs import find_exec
from mapproxy.util.yaml import load_yaml_file, YAMLError
from mapproxy.util.py import memoize
from mapproxy.config.spec import validate_options, add_source_to_mapproxy_yaml_spec, add_service_to_mapproxy_yaml_spec
from mapproxy.config.validator import validate
from mapproxy.config import load_default_config, finish_base_config, defaults
from mapproxy.service.ows import OWSServer

import os
import sys
import hashlib
import warnings
from copy import deepcopy, copy
from functools import partial
from collections import OrderedDict

import logging
from urllib.parse import urlparse

log = logging.getLogger('mapproxy.config')


class ConfigurationError(Exception):
    pass


class ProxyConfiguration(object):
    def __init__(self, conf, conf_base_dir=None, seed=False, renderd=False):
        self.configuration = conf
        self.seed = seed
        self.renderd = renderd

        if conf_base_dir is None:
            conf_base_dir = os.getcwd()

        self.load_globals(conf_base_dir=conf_base_dir)
        self.load_grids()
        self.load_caches()
        self.load_sources()
        self.load_wms_root_layer()
        self.load_tile_layers()
        self.load_services()

    def load_globals(self, conf_base_dir):
        self.globals = GlobalConfiguration(conf_base_dir=conf_base_dir,
                                           conf=self.configuration.get('globals') or {},
                                           context=self)

    def load_grids(self):
        self.grids = {}
        grid_configs = dict(defaults.grids)
        grid_configs.update(self.configuration.get('grids') or {})
        for grid_name, grid_conf in grid_configs.items():
            grid_conf.setdefault('name', grid_name)
            self.grids[grid_name] = GridConfiguration(grid_conf, context=self)

    def load_caches(self):
        self.caches = OrderedDict()
        caches_conf = self.configuration.get('caches')
        if not caches_conf:
            return
        if isinstance(caches_conf, list):
            caches_conf = list_of_dicts_to_ordered_dict(caches_conf)
        for cache_name, cache_conf in caches_conf.items():
            cache_conf['name'] = cache_name
            self.caches[cache_name] = CacheConfiguration(conf=cache_conf, context=self)

    def load_sources(self):
        self.sources = SourcesCollection()
        for source_name, source_conf in (self.configuration.get('sources') or {}).items():
            source_conf['name'] = source_name
            self.sources[source_name] = SourceConfiguration.load(conf=source_conf, context=self)

    def load_tile_layers(self):
        self.layers = OrderedDict()
        layers_conf = deepcopy(self._layers_conf_dict())
        if layers_conf is None:
            return
        layers = self._flatten_layers_conf_dict(layers_conf)
        for layer_name, layer_conf in layers.items():
            layer_conf['name'] = layer_name
            self.layers[layer_name] = LayerConfiguration(conf=layer_conf, context=self)

    def _legacy_layers_conf_dict(self):
        """
        Read old style layer configuration with a dictionary where
        the key is the layer name. Optionally: a list an each layer
        is wrapped in such dictionary.

        ::
          layers:
            foo:
              title: xxx
              sources: []
            bar:
              title: xxx
              sources: []

        or

        ::

          layers:
            - foo:
               title: xxx
               sources: []
            - bar:
               title: xxx
               sources: []

        """
        warnings.warn('old layer configuration syntax is deprecated since 1.4.0. '
                      'use list of dictionaries as documented', RuntimeWarning)
        layers = []
        layers_conf = self.configuration.get('layers')
        if not layers_conf:
            return None  # TODO config error
        if isinstance(layers_conf, list):
            layers_conf = list_of_dicts_to_ordered_dict(layers_conf)
        for layer_name, layer_conf in layers_conf.items():
            layer_conf['name'] = layer_name
            layers.append(layer_conf)
        return dict(title=None, layers=layers)

    def _layers_conf_dict(self):
        """
        Returns (recursive) layer configuration as a dictionary
        in unified structure:

        ::
            {
             title: 'xxx', # required, might be None
             name: 'xxx', # optional
             # sources or layers or both are required
             sources: [],
             layers: [
                {..., ...} # more layers like this
             ]
            }

        Multiple layers will be wrapped in an unnamed root layer, if the
        first level starts with multiple layers.
        """
        layers_conf = self.configuration.get('layers')
        if layers_conf is None:
            return

        if isinstance(layers_conf, list):
            if isinstance(layers_conf[0], dict) and len(layers_conf[0].keys()) == 1:
                # looks like ordered legacy config
                layers_conf = self._legacy_layers_conf_dict()
            elif len(layers_conf) == 1 and (
                'layers' in layers_conf[0]
                or 'sources' in layers_conf[0]
                    or 'tile_sources' in layers_conf[0]):
                # single root layer in list -> remove list
                layers_conf = layers_conf[0]
            else:
                # layer list without root -> wrap in root layer
                layers_conf = dict(title=None, layers=layers_conf)

        if len(layers_conf.keys() &
               set('layers name title sources'.split())) < 2:
            # looks like unordered legacy config
            layers_conf = self._legacy_layers_conf_dict()

        return layers_conf

    def _flatten_layers_conf_dict(self, layers_conf, _layers=None):
        """
        Returns a dictionary with all layers that have a name and sources.
        Flattens the layer tree.
        """
        layers = _layers if _layers is not None else OrderedDict()

        if 'layers' in layers_conf:
            for layer in layers_conf.pop('layers'):
                self._flatten_layers_conf_dict(layer, layers)

        if 'name' in layers_conf and ('sources' in layers_conf or 'tile_sources' in layers_conf):
            layers[layers_conf['name']] = layers_conf

        return layers

    def load_wms_root_layer(self):
        self.wms_root_layer = None

        layers_conf = self._layers_conf_dict()
        if layers_conf is None:
            return
        self.wms_root_layer = WMSLayerConfiguration(layers_conf, context=self)

    def load_services(self):
        self.services = ServiceConfiguration(self.configuration.get('services', {}), context=self)

    def configured_services(self):
        with self:
            return self.services.services()

    def __enter__(self):
        # push local base_config onto config stack
        import mapproxy.config.config
        mapproxy.config.config._config.push(self.base_config)

    def __exit__(self, type, value, traceback):
        # pop local base_config from config stack
        import mapproxy.config.config
        mapproxy.config.config._config.pop()

    @property
    def base_config(self):
        return self.globals.base_config

    def config_files(self):
        """
        Returns a dictionary with all configuration filenames and there timestamps.
        Contains any included files as well (see `base` option).
        """
        return self.configuration.get('__config_files__', {})


def list_of_dicts_to_ordered_dict(dictlist):
    """
    >>> d = list_of_dicts_to_ordered_dict([{'a': 1}, {'b': 2}, {'c': 3}])
    >>> list(d.items())
    [('a', 1), ('b', 2), ('c', 3)]
    """

    result = OrderedDict()
    for d in dictlist:
        for k, v in d.items():
            result[k] = v
    return result


class ConfigurationBase(object):
    """
    Base class for all configurations.
    """
    defaults = {}

    def __init__(self, conf, context):
        """
        :param conf: the configuration part for this configurator
        :param context: the complete proxy configuration
        :type context: ProxyConfiguration
        """
        self.conf = conf
        self.context = context
        for k, v in self.defaults.items():
            if k not in self.conf:
                self.conf[k] = v


class GridConfiguration(ConfigurationBase):
    @memoize
    def tile_grid(self):
        from mapproxy.grid.tile_grid import tile_grid

        if 'base' in self.conf:
            base_grid_name = self.conf['base']
            if base_grid_name not in self.context.grids:
                raise ConfigurationError('unknown base %s for grid %s' % (base_grid_name, self.conf['name']))
            conf = self.context.grids[base_grid_name].conf.copy()
            conf.update(self.conf)
            conf.pop('base')
            self.conf = conf
        else:
            conf = self.conf
        align_with = None
        if 'align_resolutions_with' in self.conf:
            align_with_grid_name = self.conf['align_resolutions_with']
            align_with = self.context.grids[align_with_grid_name].tile_grid()

        tile_size = self.context.globals.get_value('tile_size', conf,
                                                   global_key='grid.tile_size')
        conf['tile_size'] = tuple(tile_size)
        tile_size = tuple(tile_size)

        stretch_factor = self.context.globals.get_value('stretch_factor', conf,
                                                        global_key='image.stretch_factor')
        max_shrink_factor = self.context.globals.get_value('max_shrink_factor', conf,
                                                           global_key='image.max_shrink_factor')

        if conf.get('origin') is None:
            log.warning(
                'grid %s does not have an origin. default origin will change from sw (south/west) to nw (north-west)'
                ' with MapProxy 2.0', conf['name'])

        grid = tile_grid(
            name=conf['name'],
            srs=conf.get('srs'),
            tile_size=tile_size,
            min_res=conf.get('min_res'),
            max_res=conf.get('max_res'),
            res=conf.get('res'),
            res_factor=conf.get('res_factor', 2.0),
            threshold_res=conf.get('threshold_res'),
            bbox=conf.get('bbox'),
            bbox_srs=conf.get('bbox_srs'),
            num_levels=conf.get('num_levels'),
            stretch_factor=stretch_factor,
            max_shrink_factor=max_shrink_factor,
            align_with=align_with,
            origin=conf.get('origin')
        )

        return grid


def preferred_srs(conf):
    from mapproxy.srs import SRS, PreferredSrcSRS

    preferred_conf = conf.get('preferred_src_proj', {})

    if not preferred_conf:
        return

    preferred = PreferredSrcSRS()

    for target, preferred_srcs in preferred_conf.items():
        preferred.add(SRS(target), [SRS(s) for s in preferred_srcs])

    return preferred


class GlobalConfiguration(ConfigurationBase):
    def __init__(self, conf_base_dir, conf, context):
        ConfigurationBase.__init__(self, conf, context)
        self.base_config = load_default_config()
        self._copy_conf_values(self.conf, self.base_config)
        self.base_config.conf_base_dir = conf_base_dir
        finish_base_config(self.base_config)

        self.image_options = ImageOptionsConfiguration(self.conf.get('image', {}), context)
        self.preferred_srs = preferred_srs(self.conf.get('srs', {}))
        self.renderd_address = self.get_value('renderd.address')

    def _copy_conf_values(self, d, target):
        for k, v in d.items():
            if v is None:
                continue
            if (hasattr(v, 'iteritems') or hasattr(v, 'items')) and k in target:
                self._copy_conf_values(v, target[k])
            else:
                target[k] = v

    def get_value(self, key, local={}, global_key=None, default_key=None):
        result = dotted_dict_get(key, local)
        if result is None:
            result = dotted_dict_get(global_key or key, self.conf)

        if result is None:
            result = dotted_dict_get(default_key or global_key or key, self.base_config)

        return result

    def get_path(self, key, local, global_key=None, default_key=None):
        value = self.get_value(key, local, global_key, default_key)
        if value is not None:
            value = self.abspath(value)
        return value

    def abspath(self, path):
        return os.path.join(self.base_config.conf_base_dir, path)


default_image_options = {
}


class ImageOptionsConfiguration(ConfigurationBase):
    def __init__(self, conf, context):
        ConfigurationBase.__init__(self, conf, context)
        self._init_formats()

    def _init_formats(self):
        self.formats = {}

        formats_config = default_image_options.copy()
        for format, conf in self.conf.get('formats', {}).items():
            if format in formats_config:
                tmp = formats_config[format].copy()
                tmp.update(conf)
                conf = tmp
            if 'resampling_method' in conf:
                conf['resampling'] = conf.pop('resampling_method')
            if 'encoding_options' in conf:
                self._check_encoding_options(conf['encoding_options'])
            if 'merge_method' in conf:
                warnings.warn('merge_method now defaults to composite. option no longer required',
                              DeprecationWarning)
            formats_config[format] = conf
        for format, conf in formats_config.items():
            if 'format' not in conf and format.startswith('image/'):
                conf['format'] = format
            self.formats[format] = conf

    def _check_encoding_options(self, options):
        if not options:
            return
        options = options.copy()
        jpeg_quality = options.pop('jpeg_quality', None)
        if jpeg_quality and not isinstance(jpeg_quality, int):
            raise ConfigurationError('jpeg_quality is not an integer')

        tiff_compression = options.pop('tiff_compression', None)
        if tiff_compression and tiff_compression not in ('raw', 'tiff_lzw', 'jpeg'):
            raise ConfigurationError('unknown tiff_compression')

        quantizer = options.pop('quantizer', None)
        if quantizer and quantizer not in ('fastoctree', 'mediancut'):
            raise ConfigurationError('unknown quantizer')

        if options:
            raise ConfigurationError('unknown encoding_options: %r' % options)

    def image_opts(self, image_conf, format):
        from mapproxy.image.opts import ImageOptions
        if not image_conf:
            image_conf = {}

        conf = {}
        if format in self.formats:
            conf = self.formats[format].copy()

        resampling = image_conf.get('resampling_method') or conf.get('resampling')
        if resampling is None:
            resampling = self.context.globals.get_value('image.resampling_method', {})
        transparent = image_conf.get('transparent')
        opacity = image_conf.get('opacity')
        img_format = image_conf.get('format')
        colors = image_conf.get('colors')
        mode = image_conf.get('mode')
        encoding_options = image_conf.get('encoding_options')
        if 'merge_method' in image_conf:
            warnings.warn('merge_method now defaults to composite. option no longer required',
                          DeprecationWarning)

        self._check_encoding_options(encoding_options)

        # only overwrite default if it is not None
        for k, v in dict(
                transparent=transparent, opacity=opacity, resampling=resampling,
                format=img_format, colors=colors, mode=mode, encoding_options=encoding_options,
        ).items():
            if v is not None:
                conf[k] = v

        if 'format' not in conf and format and format.startswith('image/'):
            conf['format'] = format

        # caches shall be able to store png and jpeg tiles with mixed format
        if format == 'mixed':
            conf['format'] = format
            conf['transparent'] = True

        # force 256 colors for image.paletted for backwards compat
        paletted = self.context.globals.get_value('image.paletted', self.conf)
        if conf.get('colors') is None and 'png' in conf.get('format', '') and paletted:
            conf['colors'] = 256

        opts = ImageOptions(**conf)
        return opts


def dotted_dict_get(key, d):
    """
    >>> dotted_dict_get('foo', {'foo': {'bar': 1}})
    {'bar': 1}
    >>> dotted_dict_get('foo.bar', {'foo': {'bar': 1}})
    1
    >>> dotted_dict_get('bar', {'foo': {'bar': 1}})
    """
    parts = key.split('.')
    try:
        while parts and d:
            d = d[parts.pop(0)]
    except KeyError:
        return None
    if parts:  # not completely resolved
        return None
    return d


class SourcesCollection(dict):
    """
    Collection of SourceConfigurations.
    Allows access to tagged WMS sources, e.g.
    ``sc['source_name:lyr,lyr2']`` will return the source with ``source_name``
    and set ``req.layers`` to ``lyr1,lyr2``.
    """

    def __getitem__(self, key):
        layers = None
        source_name = key
        if ':' in source_name:
            source_name, layers = source_name.split(':', 1)
        source = dict.__getitem__(self, source_name)
        if not layers:
            return source

        if source.conf.get('type') not in ('wms', 'mapserver', 'mapnik'):
            raise ConfigurationError("found ':' in: '%s'."
                                     " tagged sources only supported for WMS/Mapserver/Mapnik" % key)

        uses_req = source.conf.get('type') != 'mapnik'

        source = copy(source)
        source.conf = deepcopy(source.conf)

        if uses_req:
            supported_layers = source.conf['req'].get('layers', [])
        else:
            supported_layers = source.conf.get('layers', [])
        supported_layer_set = SourcesCollection.layer_set(supported_layers)
        layer_set = SourcesCollection.layer_set(layers)

        if supported_layer_set and not layer_set.issubset(supported_layer_set):
            raise ConfigurationError('layers (%s) not supported by source (%s)' % (
                layers, ','.join(supported_layer_set)))

        if uses_req:
            source.conf['req']['layers'] = layers
        else:
            source.conf['layers'] = layers

        return source

    def __contains__(self, key):
        source_name = key
        if ':' in source_name:
            source_name, _ = source_name.split(':', 1)
        return dict.__contains__(self, source_name)

    @staticmethod
    def layer_set(layers):
        if isinstance(layers, (list, tuple)):
            return set(layers)
        return set(layers.split(','))


class SourceConfiguration(ConfigurationBase):

    supports_meta_tiles = True

    @classmethod
    def load(cls, conf, context):
        source_type = conf['type']

        subclass = source_configuration_types.get(source_type)
        if not subclass:
            raise ConfigurationError("unknown source type '%s'" % source_type)

        return subclass(conf, context)

    @memoize
    def coverage(self):
        if 'coverage' not in self.conf:
            return None
        from mapproxy.config.coverage import load_coverage
        return load_coverage(self.conf['coverage'])

    def image_opts(self, format=None):
        if 'transparent' in self.conf:
            self.conf.setdefault('image', {})['transparent'] = self.conf['transparent']
        return self.context.globals.image_options.image_opts(self.conf.get('image', {}), format)

    def supported_srs(self):
        from mapproxy.srs import SRS, SupportedSRS

        supported_srs = [SRS(code) for code in self.conf.get('supported_srs', [])]
        if not supported_srs:
            return None
        return SupportedSRS(supported_srs, self.context.globals.preferred_srs)

    def http_client(self, url):
        from mapproxy.client.http import auth_data_from_url, HTTPClient

        http_client = None
        url, (username, password) = auth_data_from_url(url)
        insecure = ssl_ca_certs = None
        if 'https' in url:
            insecure = self.context.globals.get_value('http.ssl_no_cert_checks', self.conf)
            ssl_ca_certs = self.context.globals.get_path('http.ssl_ca_certs', self.conf)

        timeout = self.context.globals.get_value('http.client_timeout', self.conf)
        headers = self.context.globals.get_value('http.headers', self.conf)
        hide_error_details = self.context.globals.get_value('http.hide_error_details', self.conf)
        manage_cookies = self.context.globals.get_value('http.manage_cookies', self.conf)

        http_client = HTTPClient(url, username, password, insecure=insecure,
                                 ssl_ca_certs=ssl_ca_certs, timeout=timeout,
                                 headers=headers, hide_error_details=hide_error_details,
                                 manage_cookies=manage_cookies)
        return http_client, url

    @memoize
    def on_error_handler(self):
        if 'on_error' not in self.conf:
            return None
        from mapproxy.source.error import HTTPSourceErrorHandler

        error_handler = HTTPSourceErrorHandler()
        for status_code, response_conf in self.conf['on_error'].items():
            if not isinstance(status_code, int) and status_code != 'other':
                raise ConfigurationError("invalid error code %r in on_error", status_code)
            cacheable = response_conf.get('cache', False)
            color = response_conf.get('response', 'transparent')
            authorize_stale = response_conf.get('authorize_stale', False)
            if color == 'transparent':
                color = (255, 255, 255, 0)
            else:
                color = parse_color(color)
            error_handler.add_handler(status_code, color, cacheable, authorize_stale)

        return error_handler


def resolution_range(conf):
    from mapproxy.grid.resolutions import resolution_range as _resolution_range
    if 'min_res' in conf or 'max_res' in conf:
        return _resolution_range(min_res=conf.get('min_res'),
                                 max_res=conf.get('max_res'))
    if 'min_scale' in conf or 'max_scale' in conf:
        return _resolution_range(min_scale=conf.get('min_scale'),
                                 max_scale=conf.get('max_scale'))


class ArcGISSourceConfiguration(SourceConfiguration):
    source_type = ('arcgis',)

    def __init__(self, conf, context):
        SourceConfiguration.__init__(self, conf, context)

    def source(self, params=None):
        from mapproxy.client.arcgis import ArcGISClient
        from mapproxy.source.arcgis import ArcGISSource
        from mapproxy.request.arcgis import create_request

        if not self.conf.get('opts', {}).get('map', True):
            return None

        if not self.context.seed and self.conf.get('seed_only'):
            from mapproxy.source import DummySource
            return DummySource(coverage=self.coverage())

        supported_formats = [file_ext(f) for f in self.conf.get("supported_formats", [])]

        # Construct the parameters
        if params is None:
            params = {}

        request_format = self.conf['req'].get('format')
        if request_format:
            params['format'] = request_format

        request = create_request(self.conf["req"], params)
        http_client, request.url = self.http_client(request.url)
        coverage = self.coverage()
        res_range = resolution_range(self.conf)

        client = ArcGISClient(request, http_client)
        image_opts = self.image_opts(format=params.get('format'))
        return ArcGISSource(client, image_opts=image_opts, coverage=coverage,
                            res_range=res_range,
                            supported_srs=self.supported_srs(),
                            supported_formats=supported_formats or None,
                            error_handler=self.on_error_handler())

    @memoize
    def fi_source(self, params=None):
        from mapproxy.client.arcgis import ArcGISInfoClient
        from mapproxy.request.arcgis import create_identify_request
        from mapproxy.source.arcgis import ArcGISInfoSource

        if params is None:
            params = {}
        request_format = self.conf['req'].get('format')
        if request_format:
            params['format'] = request_format
        fi_source = None
        if self.conf.get('opts', {}).get('featureinfo', False):
            opts = self.conf['opts']
            tolerance = opts.get('featureinfo_tolerance', 5)
            return_geometries = opts.get('featureinfo_return_geometries', False)

            fi_request = create_identify_request(self.conf['req'], params)

            http_client, fi_request.url = self.http_client(fi_request.url)
            fi_client = ArcGISInfoClient(fi_request,
                                         supported_srs=self.supported_srs(),
                                         http_client=http_client,
                                         tolerance=tolerance,
                                         return_geometries=return_geometries,
                                         )
            fi_source = ArcGISInfoSource(fi_client)
        return fi_source


class WMSSourceConfiguration(SourceConfiguration):
    source_type = ('wms',)

    @staticmethod
    def static_legend_source(url, context):
        from mapproxy.cache.legend import LegendCache
        from mapproxy.client.wms import WMSLegendURLClient
        from mapproxy.source.wms import WMSLegendSource

        cache_dir = os.path.join(context.globals.get_path('cache.base_dir', {}),
                                 'legends')
        if url.startswith('file://') and not url.startswith('file:///'):
            prefix = 'file://'
            url = prefix + context.globals.abspath(url[7:])
        lg_client = WMSLegendURLClient(url)

        global_directory_permissions = context.globals.get_value('directory_permissions', None,
                                                     global_key='cache.directory_permissions')
        if global_directory_permissions:
            log.info(f'Using global directory permission configuration for static legend cache:'
                     f' {global_directory_permissions}')

        global_file_permissions = context.globals.get_value(
            'file_permissions', None, global_key='cache.file_permissions')
        if global_file_permissions:
            log.info(f'Using global file permission configuration for static legend cache: {global_file_permissions}')

        legend_cache = LegendCache(cache_dir=cache_dir, directory_permissions=global_directory_permissions,
                                   file_permissions=global_file_permissions)
        return WMSLegendSource([lg_client], legend_cache, static=True)

    def fi_xslt_transformer(self, conf, context):
        from mapproxy.featureinfo import XSLTransformer
        fi_transformer = None
        fi_xslt = conf.get('featureinfo_xslt')
        if fi_xslt:
            fi_xslt = context.globals.abspath(fi_xslt)
            fi_format = conf.get('featureinfo_out_format')
            if not fi_format:
                fi_format = conf.get('featureinfo_format')
            fi_transformer = XSLTransformer(fi_xslt, fi_format)
        return fi_transformer

    def image_opts(self, format=None):
        if 'transparent' not in (self.conf.get('image') or {}):
            transparent = self.conf['req'].get('transparent')
            if transparent is not None:
                transparent = bool(str(transparent).lower() == 'true')
                self.conf.setdefault('image', {})['transparent'] = transparent
        return SourceConfiguration.image_opts(self, format=format)

    def source(self, params=None):
        from mapproxy.client.wms import WMSClient
        from mapproxy.request.wms import create_request
        from mapproxy.source.wms import WMSSource

        if not self.conf.get('wms_opts', {}).get('map', True):
            return None

        if not self.context.seed and self.conf.get('seed_only'):
            from mapproxy.source import DummySource
            return DummySource(coverage=self.coverage())

        if params is None:
            params = {}

        request_format = self.conf['req'].get('format')
        if request_format:
            params['format'] = request_format

        image_opts = self.image_opts(format=params.get('format'))

        supported_formats = [file_ext(f) for f in self.conf.get('supported_formats', [])]
        version = self.conf.get('wms_opts', {}).get('version', '1.1.1')

        lock = None
        concurrent_requests = self.context.globals.get_value('concurrent_requests', self.conf,
                                                             global_key='http.concurrent_requests')
        if concurrent_requests:
            from mapproxy.util.lock import SemLock
            lock_dir = self.context.globals.get_path('cache.lock_dir', self.conf)
            lock_timeout = self.context.globals.get_value('http.client_timeout', self.conf)
            url = urlparse(self.conf['req']['url'])

            global_directory_permissions = self.context.globals.get_value('directory_permissions', self.conf,
                                                                          global_key='cache.directory_permissions')
            if global_directory_permissions:
                log.info(f'Using global directory permission configuration for concurrent file locks:'
                         f' {global_directory_permissions}')

            global_file_permissions = self.context.globals.get_value('file_permissions', self.conf,
                                                                     global_key='cache.file_permissions')
            if global_file_permissions:
                log.info(f'Using global file permission configuration for concurrent file locks:'
                         f' {global_file_permissions}')

            md5 = hashlib.new('md5', url.netloc.encode('ascii'), usedforsecurity=False)
            lock_file = os.path.join(lock_dir, md5.hexdigest() + '.lck')
            lock = lambda: SemLock(lock_file, concurrent_requests, timeout=lock_timeout,  # noqa
                                   directory_permissions=global_directory_permissions,
                                   file_permissions=global_file_permissions)

        coverage = self.coverage()
        res_range = resolution_range(self.conf)

        transparent_color = (self.conf.get('image') or {}).get('transparent_color')
        transparent_color_tolerance = self.context.globals.get_value(
            'image.transparent_color_tolerance', self.conf)
        if transparent_color:
            transparent_color = parse_color(transparent_color)

        http_method = self.context.globals.get_value('http.method', self.conf)

        fwd_req_params = set(self.conf.get('forward_req_params', []))

        request = create_request(self.conf['req'], params, version=version,
                                 abspath=self.context.globals.abspath)
        http_client, request.url = self.http_client(request.url)
        client = WMSClient(request, http_client=http_client,
                           http_method=http_method, lock=lock,
                           fwd_req_params=fwd_req_params)
        return WMSSource(client, image_opts=image_opts, coverage=coverage,
                         res_range=res_range, transparent_color=transparent_color,
                         transparent_color_tolerance=transparent_color_tolerance,
                         supported_srs=self.supported_srs(),
                         supported_formats=supported_formats or None,
                         fwd_req_params=fwd_req_params,
                         error_handler=self.on_error_handler())

    def fi_source(self, params=None):
        from mapproxy.client.wms import WMSInfoClient
        from mapproxy.request.wms import create_request
        from mapproxy.source.wms import WMSInfoSource

        if params is None:
            params = {}
        request_format = self.conf['req'].get('format')
        if request_format:
            params['format'] = request_format
        fi_source = None
        if self.conf.get('wms_opts', {}).get('featureinfo', False):
            wms_opts = self.conf['wms_opts']
            version = wms_opts.get('version', '1.1.1')
            if 'featureinfo_format' in wms_opts:
                params['info_format'] = wms_opts['featureinfo_format']
            fi_request = create_request(self.conf['req'], params,
                                        req_type='featureinfo', version=version,
                                        abspath=self.context.globals.abspath)

            fi_transformer = self.fi_xslt_transformer(self.conf.get('wms_opts', {}),
                                                      self.context)

            http_client, fi_request.url = self.http_client(fi_request.url)
            fi_client = WMSInfoClient(fi_request, supported_srs=self.supported_srs(),
                                      http_client=http_client)
            coverage = self.coverage()
            fi_source = WMSInfoSource(fi_client, fi_transformer=fi_transformer,
                                      coverage=coverage)
        return fi_source

    def lg_source(self, params=None):
        from mapproxy.cache.legend import LegendCache
        from mapproxy.client.wms import WMSLegendClient
        from mapproxy.request.wms import create_request
        from mapproxy.source.wms import WMSLegendSource

        if params is None:
            params = {}
        request_format = self.conf['req'].get('format')
        if request_format:
            params['format'] = request_format
        lg_source = None
        cache_dir = os.path.join(self.context.globals.get_path('cache.base_dir', {}),
                                 'legends')

        if self.conf.get('wms_opts', {}).get('legendurl', False):
            lg_url = self.conf.get('wms_opts', {}).get('legendurl')
            lg_source = WMSSourceConfiguration.static_legend_source(lg_url, self.context)
        elif self.conf.get('wms_opts', {}).get('legendgraphic', False):
            version = self.conf.get('wms_opts', {}).get('version', '1.1.1')
            lg_req = self.conf['req'].copy()
            lg_clients = []
            lg_layers = str(lg_req['layers']).split(',')
            del lg_req['layers']
            for lg_layer in lg_layers:
                lg_req['layer'] = lg_layer
                lg_request = create_request(lg_req, params,
                                            req_type='legendgraphic', version=version,
                                            abspath=self.context.globals.abspath)
                http_client, lg_request.url = self.http_client(lg_request.url)
                lg_client = WMSLegendClient(lg_request, http_client=http_client)
                lg_clients.append(lg_client)

            global_directory_permissions = self.context.globals.get_value('directory_permissions', self.conf,
                                                                   global_key='cache.directory_permissions')
            if global_directory_permissions:
                log.info(f'Using global directory permission configuration for legend cache:'
                         f' {global_directory_permissions}')

            global_file_permissions = self.context.globals.get_value('file_permissions', self.conf,
                                                              global_key='cache.file_permissions')
            if global_file_permissions:
                log.info(f'Using global file permission configuration for legend cache:'
                         f' {global_file_permissions}')

            legend_cache = LegendCache(cache_dir=cache_dir, directory_permissions=global_directory_permissions,
                                       file_permissions=global_file_permissions)
            lg_source = WMSLegendSource(lg_clients, legend_cache)
        return lg_source


class MapServerSourceConfiguration(WMSSourceConfiguration):
    source_type = ('mapserver',)

    def __init__(self, conf, context):
        WMSSourceConfiguration.__init__(self, conf, context)
        self.script = self.context.globals.get_path('mapserver.binary',
                                                    self.conf)
        if not self.script:
            self.script = find_exec('mapserv')

        if not self.script or not os.path.isfile(self.script):
            raise ConfigurationError('could not find mapserver binary (%r)' %
                                     (self.script, ))

        # set url to dummy script name, required as identifier
        # for concurrent_request
        self.conf['req']['url'] = 'mapserver://' + self.script

        mapfile = self.context.globals.abspath(self.conf['req']['map'])
        self.conf['req']['map'] = mapfile

    def http_client(self, url):
        working_dir = self.context.globals.get_path('mapserver.working_dir', self.conf)
        if working_dir and not os.path.isdir(working_dir):
            raise ConfigurationError('could not find mapserver working_dir (%r)' % (working_dir, ))

        from mapproxy.client.cgi import CGIClient
        client = CGIClient(script=self.script, working_directory=working_dir)
        return client, url


class MapnikSourceConfiguration(SourceConfiguration):
    source_type = ('mapnik',)

    def source(self, params=None):
        if not self.context.seed and self.conf.get('seed_only'):
            from mapproxy.source import DummySource
            return DummySource(coverage=self.coverage())

        image_opts = self.image_opts()

        lock = None
        concurrent_requests = self.context.globals.get_value('concurrent_requests', self.conf,
                                                             global_key='http.concurrent_requests')
        if concurrent_requests:
            from mapproxy.util.lock import SemLock
            lock_dir = self.context.globals.get_path('cache.lock_dir', self.conf)
            mapfile = self.conf['mapfile']

            global_directory_permissions = self.context.globals.get_value('directory_permissions', self.conf,
                                                                          global_key='cache.directory_permissions')
            if global_directory_permissions:
                log.info(f'Using global directory permission configuration for concurrent file locks:'
                         f' {global_directory_permissions}')

            global_file_permissions = self.context.globals.get_value('file_permissions', self.conf,
                                                                     global_key='cache.file_permissions')
            if global_file_permissions:
                log.info(f'Using global file permission configuration for concurrent file locks:'
                         f' {global_file_permissions}')

            md5 = hashlib.new('md5', mapfile.encode('utf-8'), usedforsecurity=False)
            lock_file = os.path.join(lock_dir, md5.hexdigest() + '.lck')
            lock = lambda: SemLock(lock_file, concurrent_requests, # noqa
                                   directory_permissions=global_directory_permissions,
                                   file_permissions=global_file_permissions)

        coverage = self.coverage()
        res_range = resolution_range(self.conf)

        scale_factor = self.conf.get('scale_factor', None)
        multithreaded = self.conf.get('multithreaded', False)

        layers = self.conf.get('layers', None)
        if isinstance(layers, str):
            layers = layers.split(',')

        mapfile = self.context.globals.abspath(self.conf['mapfile'])

        if self.conf.get('use_mapnik2', False):
            warnings.warn('use_mapnik2 option is no longer needed for Mapnik 2 support',
                          DeprecationWarning)

        from mapproxy.source.mapnik import MapnikSource, mapnik as mapnik_api
        if mapnik_api is None:
            raise ConfigurationError('Could not import Mapnik, please verify it is installed!')

        if self.context.renderd:
            # only renderd guarantees that we have a single proc/thread
            # that accesses the same mapnik map object
            reuse_map_objects = True
        else:
            reuse_map_objects = False

        return MapnikSource(mapfile, layers=layers, image_opts=image_opts,
                            coverage=coverage, res_range=res_range, lock=lock,
                            reuse_map_objects=reuse_map_objects, scale_factor=scale_factor,
                            multithreaded=multithreaded)


class TileSourceConfiguration(SourceConfiguration):
    supports_meta_tiles = False
    source_type = ('tile',)
    defaults = {}

    def source(self, params=None):
        from mapproxy.client.tile import TileClient, TileURLTemplate
        from mapproxy.source.tile import TiledSource

        if not self.context.seed and self.conf.get('seed_only'):
            from mapproxy.source import DummySource
            return DummySource(coverage=self.coverage())

        if params is None:
            params = {}

        url = self.conf['url']

        if self.conf.get('origin'):
            warnings.warn('origin for tile sources is deprecated since 1.3.0 '
                          'and will be ignored. use grid with correct origin.', RuntimeWarning)

        http_client, url = self.http_client(url)

        grid_name = self.conf.get('grid')
        if grid_name is None:
            log.warning(
                "tile source for %s does not have a grid configured and defaults to GLOBAL_MERCATOR. default will"
                " change with MapProxy 2.0", url)
            grid_name = "GLOBAL_MERCATOR"

        grid = self.context.grids[grid_name].tile_grid()
        coverage = self.coverage()
        res_range = resolution_range(self.conf)

        image_opts = self.image_opts()
        error_handler = self.on_error_handler()

        format = file_ext(params['format'])
        client = TileClient(TileURLTemplate(url, format=format), http_client=http_client, grid=grid)
        return TiledSource(grid, client, coverage=coverage, image_opts=image_opts,
                           error_handler=error_handler, res_range=res_range)


def file_ext(mimetype):
    from mapproxy.request.base import split_mime_type
    _mime_class, format, _options = split_mime_type(mimetype)
    return format


class DebugSourceConfiguration(SourceConfiguration):
    source_type = ('debug',)
    required_keys = set('type'.split())

    def source(self, params=None):
        from mapproxy.source import DebugSource
        return DebugSource()


source_configuration_types = {
    'wms': WMSSourceConfiguration,
    'arcgis': ArcGISSourceConfiguration,
    'tile': TileSourceConfiguration,
    'debug': DebugSourceConfiguration,
    'mapserver': MapServerSourceConfiguration,
    'mapnik': MapnikSourceConfiguration,
}


def register_source_configuration(config_name, config_class,
                                  yaml_spec_source_name=None, yaml_spec_source_def=None):
    """ Method used by plugins to register a new source configuration.

        :param config_name: Name of the source configuration
        :type config_name: str
        :param config_class: Class of the source configuration
        :type config_name: SourceConfiguration
        :param yaml_spec_source_name: Name of the source in the YAML configuration file
        :type yaml_spec_source_name: str
        :param yaml_spec_source_def: Definition of the source in the YAML configuration file
        :type yaml_spec_source_def: dict

        Example:
            register_source_configuration('hips', HIPSSourceConfiguration,
                                          'hips', { required('url'): str(),
                                                    'resampling_method': str(),
                                                    'image': image_opts,
                                                  })
    """
    log.info('Registering configuration for plugin source %s' % config_name)
    source_configuration_types[config_name] = config_class
    if yaml_spec_source_name is not None and yaml_spec_source_def is not None:
        add_source_to_mapproxy_yaml_spec(yaml_spec_source_name, yaml_spec_source_def)


class CacheConfiguration(ConfigurationBase):
    defaults = {'format': 'image/png'}

    @memoize
    def coverage(self):
        if 'cache' not in self.conf or 'coverage' not in self.conf['cache']:
            return None
        from mapproxy.config.coverage import load_coverage
        return load_coverage(self.conf['cache']['coverage'])

    @memoize
    def cache_dir(self):
        cache_dir = self.conf.get('cache', {}).get('directory')
        if cache_dir:
            if self.conf.get('cache_dir'):
                log.warning('found cache.directory and cache_dir option for %s, ignoring cache_dir',
                            self.conf['name'])
            return self.context.globals.abspath(cache_dir)

        return self.context.globals.get_path('cache_dir', self.conf,
                                             global_key='cache.base_dir')

    @memoize
    def directory_permissions(self):
        directory_permissions = self.conf.get('cache', {}).get('directory_permissions')
        if directory_permissions:
            log.info('Using cache specific directory permission configuration for %s: %s',
                     self.conf['name'], directory_permissions)
            return directory_permissions

        global_permissions = self.context.globals.get_value('directory_permissions', self.conf,
                global_key='cache.directory_permissions')
        if global_permissions:
            log.info('Using global directory permission configuration for %s: %s',
                 self.conf['name'], global_permissions)
        return global_permissions

    @memoize
    def file_permissions(self):
        file_permissions = self.conf.get('cache', {}).get('file_permissions')
        if file_permissions:
            log.info('Using cache specific file permission configuration for %s: %s',
                     self.conf['name'], file_permissions)
            return file_permissions

        global_permissions = self.context.globals.get_value('file_permissions', self.conf,
                global_key='cache.file_permissions')
        if global_permissions:
            log.info('Using global file permission configuration for %s: %s',
                 self.conf['name'], global_permissions)
        return global_permissions

    @memoize
    def has_multiple_grids(self):
        return len(self.grid_confs()) > 1

    def lock_dir(self):
        lock_dir = self.context.globals.get_path('cache.tile_lock_dir', self.conf)
        if not lock_dir:
            lock_dir = os.path.join(self.cache_dir(), 'tile_locks')
        return lock_dir

    def _file_cache(self, grid_conf, image_opts):
        from mapproxy.cache.file import FileCache

        cache_dir = self.cache_dir()
        directory_layout = self.conf.get('cache', {}).get('directory_layout', 'tc')
        coverage = self.coverage()

        if self.conf.get('cache', {}).get('directory'):
            if self.has_multiple_grids():
                raise ConfigurationError(
                    "using single directory for cache with multiple grids in %s" %
                    (self.conf['name']),
                )
            pass
        elif self.conf.get('cache', {}).get('use_grid_names'):
            cache_dir = os.path.join(cache_dir, self.conf['name'], grid_conf.tile_grid().name)
        else:
            suffix = grid_conf.conf['srs'].replace(':', '')
            cache_dir = os.path.join(cache_dir, self.conf['name'] + '_' + suffix)
        link_single_color_images = self.context.globals.get_value('link_single_color_images', self.conf,
                                                                  global_key='cache.link_single_color_images')

        if link_single_color_images and sys.platform == 'win32':
            log.warning('link_single_color_images not supported on windows')
            link_single_color_images = False

        return FileCache(
            cache_dir,
            file_ext=image_opts.format.ext,
            image_opts=image_opts,
            directory_layout=directory_layout,
            link_single_color_images=link_single_color_images,
            coverage=coverage,
            directory_permissions=self.directory_permissions(),
            file_permissions=self.file_permissions()
        )

    def _mbtiles_cache(self, grid_conf, image_opts):
        from mapproxy.cache.mbtiles import MBTilesCache

        filename = self.conf['cache'].get('filename')
        if not filename:
            filename = self.conf['name'] + '.mbtiles'

        if filename.startswith('.' + os.sep):
            mbfile_path = self.context.globals.abspath(filename)
        else:
            mbfile_path = os.path.join(self.cache_dir(), filename)

        sqlite_timeout = self.context.globals.get_value('cache.sqlite_timeout', self.conf)
        wal = self.context.globals.get_value('cache.sqlite_wal', self.conf)
        coverage = self.coverage()

        return MBTilesCache(
            mbfile_path,
            timeout=sqlite_timeout,
            wal=wal,
            coverage=coverage,
            directory_permissions=self.directory_permissions(),
            file_permissions=self.file_permissions()
        )

    def _geopackage_cache(self, grid_conf, image_opts):
        from mapproxy.cache.geopackage import GeopackageCache, GeopackageLevelCache

        filename = self.conf['cache'].get('filename')
        table_name = self.conf['cache'].get('table_name') or \
            "{}_{}".format(self.conf['name'], grid_conf.tile_grid().name)
        levels = self.conf['cache'].get('levels')
        coverage = self.coverage()

        if not filename:
            filename = self.conf['name'] + '.gpkg'
        if filename.startswith('.' + os.sep):
            gpkg_file_path = self.context.globals.abspath(filename)
        else:
            gpkg_file_path = os.path.join(self.cache_dir(), filename)

        cache_dir = self.conf['cache'].get('directory')
        if cache_dir:
            cache_dir = os.path.join(
                self.context.globals.abspath(cache_dir),
                grid_conf.tile_grid().name
            )
        else:
            cache_dir = self.cache_dir()
            cache_dir = os.path.join(
                cache_dir,
                self.conf['name'],
                grid_conf.tile_grid().name
            )

        if levels:
            return GeopackageLevelCache(
                cache_dir,
                grid_conf.tile_grid(),
                table_name,
                coverage=coverage,
                directory_permissions=self.directory_permissions(),
                file_permissions=self.file_permissions()
            )
        else:
            return GeopackageCache(
                gpkg_file_path,
                grid_conf.tile_grid(),
                table_name,
                coverage=coverage,
                directory_permissions=self.directory_permissions(),
                file_permissions=self.file_permissions()
            )

    def _azureblob_cache(self, grid_conf, image_opts):
        from mapproxy.cache.azureblob import AzureBlobCache

        container_name = self.context.globals.get_value('cache.container_name', self.conf,
                                                        global_key='cache.azureblob.container_name')
        coverage = self.coverage()

        if not container_name:
            raise ConfigurationError("no container_name configured for Azure Blob cache %s" % self.conf['name'])

        connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING", self.context.globals.get_value(
            'cache.connection_string', self.conf, global_key='cache.azureblob.connection_string'))

        if not connection_string:
            raise ConfigurationError("no connection_string configured for Azure Blob cache %s" % self.conf['name'])

        directory_layout = self.conf['cache'].get('directory_layout', 'tms')

        base_path = self.conf['cache'].get('directory', None)
        if base_path is None:
            base_path = os.path.join(self.conf['name'], grid_conf.tile_grid().name)

        return AzureBlobCache(
            base_path=base_path,
            file_ext=image_opts.format.ext,
            directory_layout=directory_layout,
            container_name=container_name,
            connection_string=connection_string,
            coverage=coverage
        )

    def _s3_cache(self, grid_conf, image_opts):
        from mapproxy.cache.s3 import S3Cache

        bucket_name = self.context.globals.get_value('cache.bucket_name', self.conf,
                                                     global_key='cache.s3.bucket_name')
        coverage = self.coverage()

        if not bucket_name:
            raise ConfigurationError("no bucket_name configured for s3 cache %s" % self.conf['name'])

        profile_name = self.context.globals.get_value('cache.profile_name', self.conf,
                                                      global_key='cache.s3.profile_name')

        region_name = self.context.globals.get_value('cache.region_name', self.conf,
                                                     global_key='cache.s3.region_name')

        endpoint_url = self.context.globals.get_value('cache.endpoint_url', self.conf,
                                                      global_key='cache.s3.endpoint_url')

        access_control_list = self.context.globals.get_value('cache.access_control_list', self.conf,
                                                             global_key='cache.s3.access_control_list')

        use_http_get = self.context.globals.get_value('cache.use_http_get', self.conf,
                                                      global_key='cache.s3.use_http_get'
                                                      )

        include_grid_name = self.context.globals.get_value('cache.include_grid_name', self.conf,
                                                      global_key='cache.s3.include_grid_name')

        directory_layout = self.conf['cache'].get('directory_layout', 'tms')

        base_path = self.conf['cache'].get('directory', None)

        if include_grid_name and base_path:
            base_path = os.path.join(base_path, grid_conf.tile_grid().name)

        if base_path is None:
            base_path = os.path.join(self.conf['name'], grid_conf.tile_grid().name)

        return S3Cache(
            base_path=base_path,
            file_ext=image_opts.format.ext,
            directory_layout=directory_layout,
            bucket_name=bucket_name,
            profile_name=profile_name,
            region_name=region_name,
            endpoint_url=endpoint_url,
            access_control_list=access_control_list,
            coverage=coverage,
            use_http_get=use_http_get
        )

    def _sqlite_cache(self, grid_conf, image_opts):
        from mapproxy.cache.mbtiles import MBTilesLevelCache

        cache_dir = self.conf.get('cache', {}).get('directory')
        if cache_dir:
            cache_dir = os.path.join(
                self.context.globals.abspath(cache_dir),
                grid_conf.tile_grid().name
            )
        else:
            cache_dir = self.cache_dir()
            cache_dir = os.path.join(
                cache_dir,
                self.conf['name'],
                grid_conf.tile_grid().name
            )

        sqlite_timeout = self.context.globals.get_value('cache.sqlite_timeout', self.conf)
        wal = self.context.globals.get_value('cache.sqlite_wal', self.conf)
        coverage = self.coverage()

        return MBTilesLevelCache(
            cache_dir,
            timeout=sqlite_timeout,
            wal=wal,
            ttl=self.conf.get('cache', {}).get('ttl', 0),
            coverage=coverage,
            directory_permissions=self.directory_permissions(),
            file_permissions=self.file_permissions()
        )

    def _couchdb_cache(self, grid_conf, image_opts):
        from mapproxy.cache.couchdb import CouchDBCache, CouchDBMDTemplate

        db_name = self.conf['cache'].get('db_name')
        if not db_name:
            suffix = grid_conf.conf['srs'].replace(':', '')
            db_name = self.conf['name'] + '_' + suffix

        url = self.conf['cache'].get('url')
        if not url:
            url = 'http://127.0.0.1:5984'

        md_template = CouchDBMDTemplate(self.conf['cache'].get('tile_metadata', {}))
        tile_id = self.conf['cache'].get('tile_id')
        coverage = self.coverage()

        return CouchDBCache(
            url=url,
            db_name=db_name,
            file_ext=image_opts.format.ext,
            tile_grid=grid_conf.tile_grid(),
            md_template=md_template,
            tile_id_template=tile_id,
            coverage=coverage
        )

    def _redis_cache(self, grid_conf, image_opts):
        from mapproxy.cache.redis import RedisCache

        host = self.conf['cache'].get('host', '127.0.0.1')
        port = self.conf['cache'].get('port', 6379)
        db = self.conf['cache'].get('db', 0)
        ttl = self.conf['cache'].get('default_ttl', 3600)
        username = self.conf['cache'].get('username', None)
        password = self.conf['cache'].get('password', None)
        coverage = self.coverage()
        ssl_certfile = self.conf['cache'].get('ssl_certfile', None)
        ssl_keyfile = self.conf['cache'].get('ssl_keyfile', None)
        ssl_ca_certs = self.conf['cache'].get('ssl_ca_certs', None)
        prefix = self.conf['cache'].get('prefix')
        if not prefix:
            prefix = self.conf['name'] + '_' + grid_conf.tile_grid().name

        return RedisCache(
            host=host,
            port=port,
            db=db,
            username=username,
            password=password,
            prefix=prefix,
            ttl=ttl,
            coverage=coverage,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
            ssl_ca_certs=ssl_ca_certs
        )

    def _compact_cache(self, grid_conf, image_opts):
        from mapproxy.cache.compact import CompactCacheV1, CompactCacheV2

        coverage = self.coverage()
        cache_dir = self.cache_dir()
        if self.conf.get('cache', {}).get('directory'):
            if self.has_multiple_grids():
                raise ConfigurationError(
                    "using single directory for cache with multiple grids in %s" %
                    (self.conf['name']),
                )
            pass
        else:
            cache_dir = os.path.join(cache_dir, self.conf['name'], grid_conf.tile_grid().name)

        version = self.conf['cache']['version']
        if version == 1:
            return CompactCacheV1(
                cache_dir=cache_dir,
                coverage=coverage,
                directory_permissions=self.directory_permissions(),
                file_permissions=self.file_permissions()
            )
        elif version == 2:
            return CompactCacheV2(
                cache_dir=cache_dir,
                coverage=coverage,
                directory_permissions=self.directory_permissions(),
                file_permissions=self.file_permissions()
            )

        raise ConfigurationError("compact cache only supports version 1 or 2")

    def _tile_cache(self, grid_conf, image_opts):
        if self.conf.get('disable_storage', False):
            from mapproxy.cache.dummy import DummyCache
            return DummyCache()

        grid_conf.tile_grid()  # create to resolve `base` in grid_conf.conf
        cache_type = self.conf.get('cache', {}).get('type', 'file')
        return getattr(self, '_%s_cache' % cache_type)(grid_conf, image_opts)

    def _tile_filter(self):
        filters = []
        if 'watermark' in self.conf:
            from mapproxy.tilefilter import create_watermark_filter
            if self.conf['watermark'].get('color'):
                self.conf['watermark']['color'] = parse_color(self.conf['watermark']['color'])
            f = create_watermark_filter(self.conf, self.context)
            if f:
                filters.append(f)
        return filters

    @memoize
    def image_opts(self):
        from mapproxy.image.opts import ImageFormat

        format = None
        if 'format' not in self.conf.get('image', {}):
            format = self.conf.get('format') or self.conf.get('request_format')
        image_opts = self.context.globals.image_options.image_opts(self.conf.get('image', {}), format)
        if image_opts.format is None:
            if format is not None and format.startswith('image/'):
                image_opts.format = ImageFormat(format)
            else:
                image_opts.format = ImageFormat('image/png')
        return image_opts

    def supports_tiled_only_access(self, params=None, tile_grid=None):
        caches = self.caches()
        if len(caches) > 1:
            return False

        cache_grid, extent, tile_manager = caches[0]
        image_opts = self.image_opts()

        if (tile_grid.is_subset_of(cache_grid)
                and params.get('format') == image_opts.format):
            return True

        return False

    def source(self, params=None, tile_grid=None, tiled_only=False):
        from mapproxy.source.tile import CacheSource
        from mapproxy.layer import map_extent_from_grid

        caches = self.caches()
        if len(caches) > 1:
            # cache with multiple grids/sources
            source = self.map_layer()
            source.supports_meta_tiles = True
            return source

        cache_grid, extent, tile_manager = caches[0]
        image_opts = self.image_opts()

        cache_extent = map_extent_from_grid(tile_grid)
        cache_extent = extent.intersection(cache_extent)

        source = CacheSource(tile_manager, extent=cache_extent,
                             image_opts=image_opts, tiled_only=tiled_only)
        return source

    def _sources_for_grid(self, source_names, grid_conf, request_format):
        sources = []
        source_image_opts = []

        # a cache can directly access source tiles when _all_ sources are caches too
        # and when they have compatible grids by using tiled_only on the CacheSource
        # check if all sources support tiled_only
        tiled_only = True
        for source_name in source_names:
            if source_name in self.context.sources:
                tiled_only = False
                break
            elif source_name in self.context.caches:
                cache_conf = self.context.caches[source_name]
                tiled_only = cache_conf.supports_tiled_only_access(
                    params={'format': request_format},
                    tile_grid=grid_conf.tile_grid(),
                )
                if not tiled_only:
                    break

        for source_name in source_names:
            if source_name in self.context.sources:
                source_conf = self.context.sources[source_name]
                source = source_conf.source({'format': request_format})
            elif source_name in self.context.caches:
                cache_conf = self.context.caches[source_name]
                source = cache_conf.source(
                    params={'format': request_format},
                    tile_grid=grid_conf.tile_grid(),
                    tiled_only=tiled_only,
                )
            else:
                raise ConfigurationError('unknown source %s' % source_name)
            if source:
                sources.append(source)
                source_image_opts.append(source.image_opts)

        return sources, source_image_opts

    def _sources_for_band_merge(self, sources_conf, grid_conf, request_format):
        from mapproxy.image.merge import BandMerger

        source_names = []

        for band, band_sources in sources_conf.items():
            for source in band_sources:
                name = source['source']
                if name in source_names:
                    idx = source_names.index(name)
                else:
                    source_names.append(name)
                    idx = len(source_names) - 1

                source["src_idx"] = idx

        sources, source_image_opts = self._sources_for_grid(
            source_names=source_names,
            grid_conf=grid_conf,
            request_format=request_format,
        )

        if 'l' in sources_conf:
            mode = 'L'
        elif 'a' in sources_conf:
            mode = 'RGBA'
        else:
            mode = 'RGB'

        band_merger = BandMerger(mode=mode)
        available_bands = {'r': 0, 'g': 1, 'b': 2, 'a': 3, 'l': 0}
        for band, band_sources in sources_conf.items():
            band_idx = available_bands.get(band)
            if band_idx is None:
                raise ConfigurationError("unsupported band '%s' for cache %s"
                                         % (band, self.conf['name']))
            for source in band_sources:
                band_merger.add_ops(
                    dst_band=band_idx,
                    src_img=source['src_idx'],
                    src_band=source['band'],
                    factor=source.get('factor', 1.0),
                )

        return band_merger, sources, source_image_opts

    @memoize
    def caches(self):
        from mapproxy.cache.dummy import DummyCache, DummyLocker
        from mapproxy.cache.tile import TileManager
        from mapproxy.cache.base import TileLocker
        from mapproxy.image.opts import compatible_image_options
        from mapproxy.layer import map_extent_from_grid, merge_layer_extents

        base_image_opts = self.image_opts()
        if (self.conf.get('format') == 'mixed' and
                self.conf.get('request_format') not in ['image/png', 'image/vnd.jpeg-png']):
            raise ConfigurationError(
                'request_format must be set to image/png or image/vnd.jpeg-png if mixed mode is enabled')
        request_format = self.conf.get('request_format') or self.conf.get('format')
        if '/' in request_format:
            request_format_ext = request_format.split('/', 1)[1]
        else:
            request_format_ext = request_format

        caches = []

        meta_buffer = self.context.globals.get_value('meta_buffer', self.conf,
                                                     global_key='cache.meta_buffer')
        meta_size = self.context.globals.get_value('meta_size', self.conf,
                                                   global_key='cache.meta_size')
        bulk_meta_tiles = self.context.globals.get_value('bulk_meta_tiles', self.conf,
                                                         global_key='cache.bulk_meta_tiles')
        minimize_meta_requests = self.context.globals.get_value('minimize_meta_requests', self.conf,
                                                                global_key='cache.minimize_meta_requests')
        concurrent_tile_creators = self.context.globals.get_value('concurrent_tile_creators', self.conf,
                                                                  global_key='cache.concurrent_tile_creators')

        cache_rescaled_tiles = self.conf.get('cache_rescaled_tiles')
        upscale_tiles = self.conf.get('upscale_tiles', 0)
        if upscale_tiles < 0:
            raise ConfigurationError("upscale_tiles must be positive")
        downscale_tiles = self.conf.get('downscale_tiles', 0)
        if downscale_tiles < 0:
            raise ConfigurationError("downscale_tiles must be positive")
        if upscale_tiles and downscale_tiles:
            raise ConfigurationError("cannot use both upscale_tiles and downscale_tiles")

        rescale_tiles = 0
        if upscale_tiles:
            rescale_tiles = -upscale_tiles
        if downscale_tiles:
            rescale_tiles = downscale_tiles

        renderd_address = self.context.globals.get_value('renderd.address', self.conf)

        band_merger = None
        for grid_name, grid_conf in self.grid_confs():
            if isinstance(self.conf['sources'], dict):
                band_merger, sources, source_image_opts = self._sources_for_band_merge(
                    self.conf['sources'],
                    grid_conf=grid_conf,
                    request_format=request_format,
                )
            else:
                sources, source_image_opts = self._sources_for_grid(
                    self.conf['sources'],
                    grid_conf=grid_conf,
                    request_format=request_format,
                )

            if not sources:
                from mapproxy.source import DummySource
                sources = [DummySource()]
                source_image_opts.append(sources[0].image_opts)
            tile_grid = grid_conf.tile_grid()
            tile_filter = self._tile_filter()
            image_opts = compatible_image_options(source_image_opts, base_opts=base_image_opts)
            cache = self._tile_cache(grid_conf, image_opts)
            identifier = self.conf['name'] + '_' + tile_grid.name

            tile_creator_class = None

            use_renderd = bool(renderd_address)
            if self.context.renderd:
                # we _are_ renderd
                use_renderd = False
            if self.conf.get('disable_storage', False):
                # can't ask renderd to create tiles that shouldn't be cached
                use_renderd = False

            if use_renderd:
                from mapproxy.cache.renderd import RenderdTileCreator, has_renderd_support
                if not has_renderd_support():
                    raise ConfigurationError("renderd requires requests library")
                if self.context.seed:
                    priority = 10
                else:
                    priority = 100

                cache_dir = self.cache_dir()

                lock_dir = self.context.globals.get_value('cache.tile_lock_dir')
                if not lock_dir:
                    lock_dir = os.path.join(cache_dir, 'tile_locks')

                global_directory_permissions = self.context.globals.get_value('directory_permissions', self.conf,
                                                                         global_key='cache.directory_permissions')
                if global_directory_permissions:
                    log.info(f'Using global directory permission configuration for tile locks:'
                             f' {global_directory_permissions}')

                global_file_permissions = self.context.globals.get_value('file_permissions', self.conf,
                                                                         global_key='cache.file_permissions')
                if global_file_permissions:
                    log.info(f'Using global file permission configuration for tile locks:'
                             f' {global_file_permissions}')

                lock_timeout = self.context.globals.get_value('http.client_timeout', {})
                locker = TileLocker(lock_dir, lock_timeout, identifier + '_renderd',
                                    directory_permissions=global_directory_permissions,
                                    file_permissions=global_file_permissions)
                # TODO band_merger
                tile_creator_class = partial(RenderdTileCreator, renderd_address,
                                             priority=priority, tile_locker=locker)

            else:
                from mapproxy.cache.tile import TileCreator
                tile_creator_class = partial(TileCreator, image_merger=band_merger)

            if isinstance(cache, DummyCache):
                locker = DummyLocker()
            else:
                global_directory_permissions = self.context.globals.get_value('directory_permissions', self.conf,
                                                                              global_key='cache.directory_permissions')
                if global_directory_permissions:
                    log.info(f'Using global directory permission configuration for tile locks:'
                             f' {global_directory_permissions}')

                global_file_permissions = self.context.globals.get_value('file_permissions', self.conf,
                                                                         global_key='cache.file_permissions')
                if global_file_permissions:
                    log.info(f'Using global file permission configuration for tile locks:'
                             f' {global_file_permissions}')

                locker = TileLocker(
                    lock_dir=self.lock_dir(),
                    lock_timeout=self.context.globals.get_value('http.client_timeout', {}),
                    lock_cache_id=cache.lock_cache_id,
                    directory_permissions=global_directory_permissions,
                    file_permissions=global_file_permissions
                )

            mgr = TileManager(tile_grid, cache, sources, image_opts.format.ext,
                              locker=locker,
                              image_opts=image_opts, identifier=identifier,
                              request_format=request_format_ext,
                              meta_size=meta_size, meta_buffer=meta_buffer,
                              minimize_meta_requests=minimize_meta_requests,
                              concurrent_tile_creators=concurrent_tile_creators,
                              pre_store_filter=tile_filter,
                              tile_creator_class=tile_creator_class,
                              bulk_meta_tiles=bulk_meta_tiles,
                              cache_rescaled_tiles=cache_rescaled_tiles,
                              rescale_tiles=rescale_tiles,
                              )
            if self.conf['name'] in self.context.caches:
                mgr._refresh_before = self.context.caches[self.conf['name']].conf.get('refresh_before', {})
            extent = merge_layer_extents(sources)
            # If the cache has a defined coverage prefer it's extent over source extent
            if cache.coverage:
                extent = cache.coverage.extent
            elif extent.is_default:
                extent = map_extent_from_grid(tile_grid)
            caches.append((tile_grid, extent, mgr))
        return caches

    @memoize
    def grid_confs(self):
        grid_names = self.conf.get('grids')
        if grid_names is None:
            log.warning(
                'cache %s does not have any grids. default will change from [GLOBAL_MERCATOR] to [GLOBAL_WEBMERCATOR]'
                ' with MapProxy 2.0', self.conf['name'])
            grid_names = ['GLOBAL_MERCATOR']
        return [(g, self.context.grids[g]) for g in grid_names]

    @memoize
    def map_layer(self):
        from mapproxy.layer import CacheMapLayer, SRSConditional, ResolutionConditional

        image_opts = self.image_opts()
        max_tile_limit = self.context.globals.get_value('max_tile_limit', self.conf,
                                                        global_key='cache.max_tile_limit')
        caches = []
        main_grid = None
        for grid, extent, tile_manager in self.caches():
            if main_grid is None:
                main_grid = grid
            caches.append((CacheMapLayer(tile_manager, extent=extent, image_opts=image_opts,
                                         max_tile_limit=max_tile_limit),
                          grid.srs))

        if len(caches) == 1:
            layer = caches[0][0]
        else:
            layer = SRSConditional(caches, caches[0][0].extent, opacity=image_opts.opacity,
                                   preferred_srs=self.context.globals.preferred_srs)

        if 'use_direct_from_level' in self.conf:
            self.conf['use_direct_from_res'] = main_grid.resolution(self.conf['use_direct_from_level'])
        if 'use_direct_from_res' in self.conf:
            if len(self.conf['sources']) != 1:
                raise ValueError('use_direct_from_level/res only supports single sources')
            source_conf = self.context.sources[self.conf['sources'][0]]
            layer = ResolutionConditional(layer, source_conf.source(), self.conf['use_direct_from_res'],
                                          main_grid.srs, layer.extent, opacity=image_opts.opacity)
        return layer


class WMSLayerConfiguration(ConfigurationBase):
    @memoize
    def wms_layer(self):
        from mapproxy.service.wms import WMSGroupLayer

        layers = []
        this_layer = None

        if 'layers' in self.conf:
            layers_conf = self.conf['layers']
            for layer_conf in layers_conf:
                lyr = WMSLayerConfiguration(layer_conf, self.context).wms_layer()
                if lyr:
                    layers.append(lyr)

        if 'sources' in self.conf or 'legendurl' in self.conf:
            this_layer = LayerConfiguration(self.conf, self.context).wms_layer()

        if not layers and not this_layer:
            return None

        if not layers:
            layer = this_layer
        else:
            layer = WMSGroupLayer(name=self.conf.get('name'), title=self.conf.get('title'),
                                  this=this_layer, layers=layers, md=self.conf.get('md'))
        return layer


def cache_source_names(context, cache):
    """
    Return all sources for a cache, even if a caches uses another cache.
    """
    source_names = []
    for src in context.caches[cache].conf['sources']:
        if src in context.caches and src not in context.sources:
            source_names.extend(cache_source_names(context, src))
        else:
            source_names.append(src)

    return source_names


class LayerConfiguration(ConfigurationBase):
    @memoize
    def wms_layer(self):
        from mapproxy.service.wms import WMSLayer

        sources = []
        fi_sources = []
        lg_sources = []

        lg_sources_configured = False
        if self.conf.get('legendurl'):
            legend_url = self.conf['legendurl']
            lg_sources.append(WMSSourceConfiguration.static_legend_source(legend_url, self.context))
            lg_sources_configured = True

        for source_name in self.conf.get('sources', []):
            fi_source_names = []
            lg_source_names = []
            if source_name in self.context.caches:
                map_layer = self.context.caches[source_name].map_layer()
                fi_source_names = cache_source_names(self.context, source_name)
                lg_source_names = cache_source_names(self.context, source_name)
            elif source_name in self.context.sources:
                source_conf = self.context.sources[source_name]
                if not source_conf.supports_meta_tiles:
                    raise ConfigurationError('source "%s" of layer "%s" does not support un-tiled access'
                                             % (source_name, self.conf.get('name')))
                map_layer = source_conf.source()
                fi_source_names = [source_name]
                lg_source_names = [source_name]
            else:
                raise ConfigurationError('source/cache "%s" not found' % source_name)

            if map_layer:
                sources.append(map_layer)

            for fi_source_name in fi_source_names:
                if fi_source_name not in self.context.sources:
                    continue
                if not hasattr(self.context.sources[fi_source_name], 'fi_source'):
                    continue
                fi_source = self.context.sources[fi_source_name].fi_source()
                if fi_source:
                    fi_sources.append(fi_source)
            if not lg_sources_configured:
                for lg_source_name in lg_source_names:
                    if lg_source_name not in self.context.sources:
                        continue
                    if not hasattr(self.context.sources[lg_source_name], 'lg_source'):
                        continue
                    lg_source = self.context.sources[lg_source_name].lg_source()
                    if lg_source:
                        lg_sources.append(lg_source)

        res_range = resolution_range(self.conf)
        dimensions = None
        if 'dimensions' in self.conf.keys():
            dimensions = self.dimensions()

        layer = WMSLayer(
            self.conf.get('name'), self.conf.get('title'), sources, fi_sources, lg_sources, res_range=res_range,
            md=self.conf.get('md'), dimensions=dimensions)
        return layer

    @memoize
    def dimensions(self):
        from mapproxy.layer import Dimension
        from mapproxy.util.ext.wmsparse.util import parse_datetime_range
        dimensions = {}
        for dimension, conf in self.conf.get('dimensions', {}).items():
            raw_values = conf.get('values')
            if len(raw_values) == 1:
                # look for time or dim_reference_time
                if 'time' in dimension.lower():
                    log.debug('Determining values as datetime strings')
                    values = parse_datetime_range(raw_values[0])
                else:
                    log.debug('Determining values as plain strings')
                    values = raw_values[0].strip().split('/')
            else:
                values = [str(val) for val in conf.get('values', ['default'])]

            default = conf.get('default', values[-1])
            dimensions[dimension.lower()] = Dimension(dimension, values, default=default)
        return dimensions

    @memoize
    def tile_layers(self, grid_name_as_path=False):
        from mapproxy.service.tile import TileLayer
        from mapproxy.cache.dummy import DummyCache
        sources = []
        fi_only_sources = []
        if 'tile_sources' in self.conf:
            sources = self.conf['tile_sources']
        else:
            for source_name in self.conf.get('sources', []):
                # we only support caches for tiled access...
                if source_name not in self.context.caches:
                    if source_name in self.context.sources:
                        src_conf = self.context.sources[source_name].conf
                        # but we ignore debug layers for convenience
                        if src_conf['type'] == 'debug':
                            continue
                        # and WMS layers with map: False (i.e. FeatureInfo only sources)
                        if src_conf['type'] == 'wms' and src_conf.get('wms_opts', {}).get('map', True) is False:
                            fi_only_sources.append(source_name)
                            continue

                    return []
                sources.append(source_name)

            if len(sources) > 1:
                # skip layers with more then one source
                return []

        dimensions = self.dimensions()

        tile_layers = []
        for cache_name in sources:
            fi_sources = []
            fi_source_names = cache_source_names(self.context, cache_name)

            for fi_source_name in fi_source_names + fi_only_sources:
                if fi_source_name not in self.context.sources:
                    continue
                if not hasattr(self.context.sources[fi_source_name], 'fi_source'):
                    continue
                fi_source = self.context.sources[fi_source_name].fi_source()
                if fi_source:
                    fi_sources.append(fi_source)

            for grid, extent, cache_source in self.context.caches[cache_name].caches():
                disable_storage = self.context.configuration['caches'][cache_name].get('disable_storage', False)
                if disable_storage:
                    supports_dimensions = isinstance(cache_source.cache, DummyCache)
                else:
                    supports_dimensions = cache_source.cache.supports_dimensions
                if dimensions and not supports_dimensions:
                    # caching of dimension layers is not supported yet
                    raise ConfigurationError(
                        "caching of dimension layer (%s) is not supported yet."
                        " need to `disable_storage: true` on %s cache" % (self.conf['name'], cache_name)
                    )

                md = {}
                md['title'] = self.conf['title']
                md['name'] = self.conf['name']
                md['grid_name'] = grid.name
                if grid_name_as_path:
                    md['name_path'] = (md['name'], md['grid_name'])
                else:
                    md['name_path'] = (md['name'], grid.srs.srs_code.replace(':', '').upper())
                md['name_internal'] = md['name_path'][0] + '_' + md['name_path'][1]
                md['format'] = self.context.caches[cache_name].image_opts().format
                md['cache_name'] = cache_name
                md['extent'] = extent
                md['wmts_kvp_legendurl'] = self.conf.get('wmts_kvp_legendurl')
                md['wmts_rest_legendurl'] = self.conf.get('wmts_rest_legendurl')
                if 'legendurl' in self.conf:
                    wms_conf = self.context.services.conf.get('wms')
                    if wms_conf is not None:
                        versions = wms_conf.get('versions', ['1.3.0'])
                        versions.sort(key=lambda s: [int(u) for u in s.split('.')])
                        legendurl = (f'{{base_url}}/service?service=WMS&amp;request=GetLegendGraphic&amp;'
                                     f'version={versions[-1]}&amp;format=image%2Fpng&amp;layer={{layer_name}}')
                        if md['wmts_kvp_legendurl'] is None:
                            md['wmts_kvp_legendurl'] = legendurl
                        if md['wmts_rest_legendurl'] is None:
                            md['wmts_rest_legendurl'] = legendurl
                tile_layers.append(
                    TileLayer(
                        self.conf['name'], self.conf['title'],
                        info_sources=fi_sources,
                        md=md,
                        tile_manager=cache_source,
                        dimensions=dimensions
                    )
                )

        return tile_layers


def fi_xslt_transformers(conf, context):
    from mapproxy.featureinfo import XSLTransformer
    fi_transformers = {}
    fi_xslt = conf.get('featureinfo_xslt')
    if fi_xslt:
        for info_type, fi_xslt in fi_xslt.items():
            fi_xslt = context.globals.abspath(fi_xslt)
            fi_transformers[info_type] = XSLTransformer(fi_xslt)
    return fi_transformers


def extents_for_srs(bbox_srs):
    from mapproxy.layer import DefaultMapExtent, MapExtent
    from mapproxy.srs import SRS
    extents = {}
    for srs in bbox_srs:
        if isinstance(srs, str):
            bbox = DefaultMapExtent()
        else:
            srs, bbox = srs['srs'], srs['bbox']
            bbox = MapExtent(bbox, SRS(srs))

        extents[srs] = bbox

    return extents


plugin_services = {}


def register_service_configuration(service_name, service_creator,
                                   yaml_spec_service_name=None, yaml_spec_service_def=None):
    """ Method used by plugins to register a new service.

        :param service_name: Name of the service
        :type service_name: str
        :param service_creator: Creator method of the service
        :type service_creator: method of type (serviceConfiguration: ServiceConfiguration, conf: dict) -> Server
        :param yaml_spec_service_name: Name of the service in the YAML configuration file
        :type yaml_spec_service_name: str
        :param yaml_spec_service_def: Definition of the service in the YAML configuration file
        :type yaml_spec_service_def: dict
    """

    log.info('Registering configuration for plugin service %s' % service_name)
    plugin_services[service_name] = service_creator
    if yaml_spec_service_name is not None and yaml_spec_service_def is not None:
        add_service_to_mapproxy_yaml_spec(yaml_spec_service_name, yaml_spec_service_def)


class ServiceConfiguration(ConfigurationBase):
    def __init__(self, conf, context):
        if 'wms' in conf:
            if conf['wms'] is None:
                conf['wms'] = {}
            if 'md' not in conf['wms']:
                conf['wms']['md'] = {'title': 'MapProxy WMS'}

        ConfigurationBase.__init__(self, conf, context)

    def services(self):
        services = []
        ows_services = []
        for service_name, service_conf in self.conf.items():
            creator = getattr(self, service_name + '_service', None)
            if not creator:
                # If not a known service, try to use the plugin mechanism
                creator = plugin_services.get(service_name, None)
                if not creator:
                    raise ValueError('unknown service: %s' % service_name)
                new_services = creator(self, service_conf or {})
            else:
                new_services = creator(service_conf or {})

            # a creator can return a list of services...
            if not isinstance(new_services, (list, tuple)):
                new_services = [new_services]

            for new_service in new_services:
                if getattr(new_service, 'service', None):
                    ows_services.append(new_service)
                else:
                    services.append(new_service)

        services.append(OWSServer(ows_services))
        return services

    def tile_layers(self, conf, use_grid_names=False):
        layers = OrderedDict()
        for layer_name, layer_conf in self.context.layers.items():
            for tile_layer in layer_conf.tile_layers(grid_name_as_path=use_grid_names):
                if not tile_layer:
                    continue
                if use_grid_names:
                    layers[tile_layer.md['name_path']] = tile_layer
                else:
                    layers[tile_layer.md['name_internal']] = tile_layer
        return layers

    def kml_service(self, conf):
        from mapproxy.service.kml import KMLServer

        md = self.context.services.conf.get('wms', {}).get('md', {}).copy()
        md.update(conf.get('md', {}))
        max_tile_age = self.context.globals.get_value('tiles.expires_hours')
        max_tile_age *= 60 * 60  # seconds
        use_grid_names = conf.get('use_grid_names', False)
        layers = self.tile_layers(conf, use_grid_names=use_grid_names)
        return KMLServer(layers, md, max_tile_age=max_tile_age, use_dimension_layers=use_grid_names)

    def tms_service(self, conf):
        from mapproxy.service.tile import TileServer

        md = self.context.services.conf.get('wms', {}).get('md', {}).copy()
        md.update(conf.get('md', {}))
        max_tile_age = self.context.globals.get_value('tiles.expires_hours')
        max_tile_age *= 60 * 60  # seconds

        origin = conf.get('origin')
        use_grid_names = conf.get('use_grid_names', False)
        layers = self.tile_layers(conf, use_grid_names=use_grid_names)
        return TileServer(layers, md, max_tile_age=max_tile_age, use_dimension_layers=use_grid_names,
                          origin=origin)

    def wmts_service(self, conf):
        from mapproxy.service.wmts import WMTSServer, WMTSRestServer

        md = self.context.services.conf.get('wms', {}).get('md', {}).copy()
        md.update(conf.get('md', {}))
        layers = self.tile_layers(conf, use_grid_names=True)

        kvp = conf.get('kvp')
        restful = conf.get('restful')

        max_tile_age = self.context.globals.get_value('tiles.expires_hours')
        max_tile_age *= 60 * 60  # seconds

        info_formats = conf.get('featureinfo_formats', [])
        info_formats = OrderedDict((f['suffix'], f['mimetype']) for f in info_formats)

        if kvp is None and restful is None:
            kvp = restful = True

        services = []
        if kvp:
            services.append(
                WMTSServer(
                    layers, md, max_tile_age=max_tile_age,
                    info_formats=info_formats,
                )
            )

        if restful:
            template = conf.get('restful_template')
            fi_template = conf.get('restful_featureinfo_template')
            if template and '{{' in template:
                # TODO remove warning in 1.6
                log.warning("double braces in WMTS restful_template are deprecated {{x}} -> {x}")
            services.append(
                WMTSRestServer(
                    layers, md, template=template,
                    fi_template=fi_template,
                    max_tile_age=max_tile_age,
                    info_formats=info_formats,
                )
            )

        return services

    def wms_service(self, conf):
        from mapproxy.service.wms import WMSServer
        from mapproxy.request.wms import Version

        md = conf.get('md', {})
        inspire_md = conf.get('inspire_md', {})
        tile_layers = self.tile_layers(conf)
        attribution = conf.get('attribution')
        strict = self.context.globals.get_value('strict', conf, global_key='wms.strict')
        on_source_errors = self.context.globals.get_value('on_source_errors',
                                                          conf, global_key='wms.on_source_errors')
        root_layer = self.context.wms_root_layer.wms_layer()
        if not root_layer:
            raise ConfigurationError("found no WMS layer")
        if not root_layer.title:
            # set title of root layer to WMS title
            root_layer.title = md.get('title')
        concurrent_layer_renderer = self.context.globals.get_value(
            'concurrent_layer_renderer', conf,
            global_key='wms.concurrent_layer_renderer')
        image_formats_names = self.context.globals.get_value('image_formats', conf,
                                                             global_key='wms.image_formats')
        image_formats = OrderedDict()
        for format in image_formats_names:
            opts = self.context.globals.image_options.image_opts({}, format)
            if opts.format in image_formats:
                log.warning('duplicate mime-type for WMS image_formats: "%s" already configured, will use last format',
                            opts.format)
            image_formats[opts.format] = opts
        info_types = conf.get('featureinfo_types')
        srs = self.context.globals.get_value('srs', conf, global_key='wms.srs')
        self.context.globals.base_config.wms.srs = srs
        srs_extents = extents_for_srs(conf.get('bbox_srs', []))

        versions = conf.get('versions')
        if versions:
            versions = sorted([Version(v) for v in versions])

        max_output_pixels = self.context.globals.get_value('max_output_pixels', conf,
                                                           global_key='wms.max_output_pixels')
        if isinstance(max_output_pixels, list):
            max_output_pixels = max_output_pixels[0] * max_output_pixels[1]

        max_tile_age = self.context.globals.get_value('tiles.expires_hours')
        max_tile_age *= 60 * 60  # seconds

        server = WMSServer(root_layer, md, attribution=attribution,
                           image_formats=image_formats, info_types=info_types,
                           srs=srs, tile_layers=tile_layers, strict=strict, on_error=on_source_errors,
                           concurrent_layer_renderer=concurrent_layer_renderer,
                           max_output_pixels=max_output_pixels, srs_extents=srs_extents,
                           max_tile_age=max_tile_age, versions=versions,
                           inspire_md=inspire_md,
                           )

        server.fi_transformers = fi_xslt_transformers(conf, self.context)

        return server

    def demo_service(self, conf):
        from mapproxy.service.demo import DemoServer
        services = list(self.context.services.conf.keys())
        md = self.context.services.conf.get('wms', {}).get('md', {}).copy()
        md.update(conf.get('md', {}))
        layers = OrderedDict()
        for layer_name, layer_conf in self.context.layers.items():
            lyr = layer_conf.wms_layer()
            if lyr:
                layers[layer_name] = lyr
        image_formats = self.context.globals.get_value('image_formats', conf, global_key='wms.image_formats')
        srs = self.context.globals.get_value('srs', conf, global_key='wms.srs')
        tms_conf = self.context.services.conf.get('tms', {}) or {}
        use_grid_names = tms_conf.get('use_grid_names', False)
        tile_layers = self.tile_layers(tms_conf, use_grid_names=use_grid_names)

        # WMTS restful template
        wmts_conf = self.context.services.conf.get('wmts', {}) or {}
        from mapproxy.service.wmts import WMTSRestServer
        if wmts_conf:
            restful_template = wmts_conf.get('restful_template', WMTSRestServer.default_template)
        else:
            restful_template = WMTSRestServer.default_template

        if 'wmts' in self.context.services.conf:
            kvp = wmts_conf.get('kvp')
            restful = wmts_conf.get('restful')

            if kvp or kvp is None:
                services.append('wmts_kvp')
            if restful or restful is None:
                services.append('wmts_restful')

        if 'wms' in self.context.services.conf:
            versions = self.context.services.conf['wms'].get('versions', ['1.1.1'])
            if '1.1.1' in versions:
                # demo service only supports 1.1.1, use wms_111 as an indicator
                services.append('wms_111')

        layers = OrderedDict(sorted(layers.items(), key=lambda x: x[1].name))
        background = self.context.globals.get_value('background', conf)

        return DemoServer(
            layers, md, tile_layers=tile_layers, image_formats=image_formats, srs=srs, services=services,
            restful_template=restful_template, background=background)


def load_plugins():
    """ Locate plugins that belong to the 'mapproxy' group and load them """
    try:
        import importlib.metadata
    except ImportError:
        return

    for dist in importlib.metadata.distributions():
        for ep in dist.entry_points:
            if ep.group == 'mapproxy':
                log.info('Loading plugin from package %s' % dist.metadata['name'])
                ep.load().plugin_entrypoint()


def load_configuration(mapproxy_conf, seed=False, ignore_warnings=True, renderd=False):

    load_plugins()

    conf_base_dir = os.path.abspath(os.path.dirname(mapproxy_conf))

    # A configuration is checked/validated four times, each step has a different
    # focus and returns different errors. The steps are:
    # 1. YAML loading: checks YAML syntax like tabs vs. space, indention errors, etc.
    # 2. Options: checks all options agains the spec and validates their types,
    #             e.g is disable_storage a bool, is layers a list, etc.
    # 3. References: checks if all referenced caches, sources and grids exist
    # 4. Initialization: creates all MapProxy objects, returns on first error

    try:
        conf_dict = load_configuration_file([os.path.basename(mapproxy_conf)], conf_base_dir)
    except YAMLError as ex:
        raise ConfigurationError(ex)
    errors, informal_only = validate_options(conf_dict)
    for error in errors:
        log.warning(error)
    if not informal_only or (errors and not ignore_warnings):
        raise ConfigurationError('invalid configuration')
    errors = validate(conf_dict)
    for error in errors:
        log.warning(error)

    services = conf_dict.get('services')
    if services is not None and 'demo' in services:
        log.warning('Application has demo page enabled. It is recommended to disable this in production.')

    return ProxyConfiguration(conf_dict, conf_base_dir=conf_base_dir, seed=seed,
                              renderd=renderd)


def load_configuration_file(files, working_dir):
    """
    Return configuration dict from imported files
    """
    # record all config files with timestamp for reloading
    conf_dict = {'__config_files__': {}}
    for conf_file in files:
        conf_file = os.path.normpath(os.path.join(working_dir, conf_file))
        log.info('reading: %s' % conf_file)
        current_dict = load_yaml_file(conf_file)
        conf_dict['__config_files__'][os.path.abspath(conf_file)] = os.path.getmtime(conf_file)
        if 'base' in current_dict:
            current_working_dir = os.path.dirname(conf_file)
            base_files = current_dict.pop('base')
            if isinstance(base_files, str):
                base_files = [base_files]
            imported_dict = load_configuration_file(base_files, current_working_dir)
            current_dict = merge_dict(current_dict, imported_dict)
        conf_dict = merge_dict(conf_dict, current_dict)

    return conf_dict


def merge_dict(conf, base):
    """
    Return `base` dict with values from `conf` merged in.
    """
    for k, v in conf.items():
        if k not in base:
            base[k] = v
        else:
            if isinstance(base[k], dict):
                if v is not None:
                    base[k] = merge_dict(v, base[k])
            elif isinstance(base[k], list):
                if v is not None:
                    if k in ['bbox', 'tile_size', 'max_output_pixels', 'sources', 'grids']:
                        base[k] = v
                    elif k in ['layers']:
                        base[k] = merge_layers(v, base[k])
                    elif len(v) == 0:  # delete
                        base[k] = None
                    else:
                        base[k] = base[k] + v
            else:
                base[k] = v
    return base


def merge_layers(conf, base):
    """
    Return `base` dict with values from `conf` merged in.
    """
    out = []
    remaining_conf = []
    for conf_layer in conf:
        remaining_conf.append(conf_layer['name'])

    for base_layer in base:
        found = False
        for conf_layer in conf:
            if conf_layer['name'] in remaining_conf and base_layer['name'] == conf_layer['name']:
                new_layer = merge_dict(conf_layer, base_layer)
                out.append(new_layer)
                remaining_conf.remove(conf_layer['name'])
                found = True
                break

        if not found:
            out.append(base_layer)

    for conf_layer in conf:
        if conf_layer['name'] in remaining_conf:
            out.append(conf_layer)

    return out


def parse_color(color):
    """
    >>> parse_color((100, 12, 55))
    (100, 12, 55)
    >>> parse_color('0xff0530')
    (255, 5, 48)
    >>> parse_color('#FF0530')
    (255, 5, 48)
    >>> parse_color('#FF053080')
    (255, 5, 48, 128)
    """
    if isinstance(color, (list, tuple)) and 3 <= len(color) <= 4:
        return tuple(color)
    if not isinstance(color, str):
        raise ValueError('color needs to be a tuple/list or 0xrrggbb/#rrggbb(aa) string, got %r' % color)

    if color.startswith('0x'):
        color = color[2:]
    if color.startswith('#'):
        color = color[1:]

    r, g, b = map(lambda x: int(x, 16), [color[:2], color[2:4], color[4:6]])

    if len(color) == 8:
        a = int(color[6:8], 16)
        return r, g, b, a

    return r, g, b
