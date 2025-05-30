# This file is part of the MapProxy project.
# Copyright (C) 2011 Omniscale <http://omniscale.de>
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

from __future__ import print_function

import datetime

from mapproxy.util.ext.dictspec.validator import validate, ValidationError
from mapproxy.util.ext.dictspec.spec import one_of, anything, number
from mapproxy.util.ext.dictspec.spec import recursive, required, type_spec, combined


def validate_options(conf_dict):
    """
    Validate `conf_dict` agains mapproxy.yaml spec.
    Returns tuple with a list of errors and a bool.
    The list is empty when no errors where found.
    The bool is True when the errors are informal and not critical.
    """
    try:
        validate(mapproxy_yaml_spec, conf_dict)
    except ValidationError as ex:
        return ex.errors, ex.informal_only
    else:
        return [], True


time_spec = {
    'seconds': number(),
    'minutes': number(),
    'hours': number(),
    'days': number(),
    'weeks': number(),
    'time': anything(),
    'mtime': str(),
}

coverage = recursive({
    'polygons': str(),
    'polygons_srs': str(),
    'bbox': one_of(str(), [number()]),
    'bbox_srs': str(),
    'ogr_datasource': str(),
    'ogr_where': str(),
    'ogr_srs': str(),
    'datasource': one_of(str(), [number()]),
    'where': str(),
    'srs': str(),
    'expire_tiles': str(),
    'union': [recursive()],
    'difference': [recursive()],
    'intersection': [recursive()],
    'clip': bool(),
})

image_opts = {
    'mode': str(),
    'colors': number(),
    'transparent': bool(),
    'resampling_method': str(),
    'format': str(),
    'encoding_options': {
        anything(): anything()
    },
    'merge_method': str(),
}

http_opts = {
    'method': str(),
    'client_timeout': number(),
    'ssl_no_cert_checks': bool(),
    'ssl_ca_certs': str(),
    'hide_error_details': bool(),
    'headers': {
        anything(): str()
    },
    'manage_cookies': bool(),
}

mapserver_opts = {
    'binary': str(),
    'working_dir': str(),
}

scale_hints = {
    'max_scale': number(),
    'min_scale': number(),
    'max_res': number(),
    'min_res': number(),
}

source_commons = combined(
    scale_hints,
    {
        'concurrent_requests': int(),
        'coverage': coverage,
        'seed_only': bool(),
    }
)

cache_commons = combined(
    {
        'coverage': coverage,
    }
)

cache_types = {
    'file': combined(cache_commons, {
        'directory_layout': str(),
        'use_grid_names': bool(),
        'directory': str(),
        'tile_lock_dir': str(),
        'directory_permissions': str(),
        'file_permissions': str(),
    }),
    'sqlite': combined(cache_commons, {
        'directory': str(),
        'sqlite_timeout': number(),
        'sqlite_wal': bool(),
        'tile_lock_dir': str(),
        'ttl': int(),
        'directory_permissions': str(),
        'file_permissions': str(),
    }),
    'mbtiles': combined(cache_commons, {
        'filename': str(),
        'sqlite_timeout': number(),
        'sqlite_wal': bool(),
        'tile_lock_dir': str(),
        'directory_permissions': str(),
        'file_permissions': str(),
    }),
    'geopackage': combined(cache_commons, {
        'filename': str(),
        'directory': str(),
        'tile_lock_dir': str(),
        'table_name': str(),
        'levels': bool(),
        'directory_permissions': str(),
        'file_permissions': str(),
    }),
    'couchdb': combined(cache_commons, {
        'url': str(),
        'db_name': str(),
        'tile_metadata': {
            anything(): anything()
        },
        'tile_id': str(),
        'tile_lock_dir': str(),
    }),
    's3': combined(cache_commons, {
        'bucket_name': str(),
        'directory_layout': str(),
        'directory': str(),
        'profile_name': str(),
        'region_name': str(),
        'endpoint_url': str(),
        'access_control_list': str(),
        'tile_lock_dir': str(),
        'use_http_get': bool(),
        'include_grid_name': bool(),
    }),
    'redis': combined(cache_commons, {
        'host': str(),
        'port': int(),
        'password': str(),
        'username': str(),
        'db': int(),
        'prefix': str(),
        'default_ttl': int(),
        'ssl_certfile': str(),
        'ssl_keyfile': str(),
        'ssl_ca_certs': str(),
    }),
    'compact': combined(cache_commons, {
        'directory': str(),
        required('version'): number(),
        'tile_lock_dir': str(),
        'directory_permissions': str(),
        'file_permissions': str(),
    }),
    'azureblob': combined(cache_commons, {
        'connection_string': str(),
        'container_name': str(),
        'directory_layout': str(),
        'directory': str(),
        'tile_lock_dir': str(),
    }),
}

on_error = {
    anything(): {
        required('response'): one_of([int], str),
        'cache': bool,
        'authorize_stale': bool
    }
}


inspire_md = {
    'linked': {
        required('metadata_url'): {
            required('url'): str,
            required('media_type'): str,
        },
        required('languages'): {
            required('default'): str,
        },
    },
    'embedded': {
        required('resource_locators'): [{
            required('url'): str,
            required('media_type'): str,
        }],
        required('temporal_reference'): {
            'date_of_publication': one_of(str, datetime.date),
            'date_of_creation': one_of(str, datetime.date),
            'date_of_last_revision': one_of(str, datetime.date),
        },
        required('conformities'): [{
            'title': str,
            'uris': [str],
            'date_of_publication': one_of(str, datetime.date),
            'date_of_creation': one_of(str, datetime.date),
            'date_of_last_revision': one_of(str, datetime.date),
            required('resource_locators'): [{
                required('url'): str,
                required('media_type'): str,
            }],
            required('degree'): str,
        }],
        required('metadata_points_of_contact'): [{
            'organisation_name': str,
            'email': str,
        }],
        required('mandatory_keywords'): [str],
        'keywords': [{
            required('title'): str,
            'date_of_publication': one_of(str, datetime.date),
            'date_of_creation': one_of(str, datetime.date),
            'date_of_last_revision': one_of(str, datetime.date),
            'uris': [str],
            'resource_locators': [{
                required('url'): str,
                required('media_type'): str,
            }],
            required('keyword_value'): str,
        }],
        required('metadata_date'): one_of(str, datetime.date),
        'metadata_url': {
            required('url'): str,
            required('media_type'): str,
        },
        required('languages'): {
            required('default'): str,
        },
    },
}

wms_130_layer_md = {
    'abstract': str,
    'keyword_list': [
        {
            'vocabulary': str,
            'keywords': [str],
        }
    ],
    'attribution': {
        'title': str,
        'url':    str,
        'logo': {
            'url':    str,
            'width':  int,
            'height': int,
            'format': str,
        }
    },
    'identifier': [
        {
            'url': str,
            'name': str,
            'value': str,
        }
    ],
    'metadata': [
        {
            'url': str,
            'type': str,
            'format': str,
        },
    ],
    'data': [
        {
            'url': str,
            'format': str,
        }

    ],
    'feature_list': [
        {
            'url': str,
            'format': str,
        }
    ],
}

grid_opts = {
    'base': str(),
    'name': str(),
    'srs': str(),
    'bbox': one_of(str(), [number()]),
    'bbox_srs': str(),
    'num_levels': int(),
    'res': [number()],
    'res_factor': one_of(number(), str()),
    'max_res': number(),
    'min_res': number(),
    'stretch_factor': number(),
    'max_shrink_factor': number(),
    'align_resolutions_with': str(),
    'origin': str(),
    'tile_size': [int()],
    'threshold_res': [number()],
}

ogc_service_md = {
    'title': str,
    'abstract': str,
    'online_resource': str,
    'contact': anything(),
    'fees': str,
    'access_constraints': str,
    'keyword_list': [
        {
            'vocabulary': str,
            'keywords': [str],
        }
    ],
}

band_source = {
    required('source'): str(),
    required('band'): int,
    'factor': number(),
}

band_sources = {
    'r': [band_source],
    'g': [band_source],
    'b': [band_source],
    'a': [band_source],
    'l': [band_source],
}

mapproxy_yaml_spec = {
    '__config_files__': anything(),  # only used internaly
    'globals': {
        'image': {
            'resampling_method': 'method',
            'paletted': bool(),
            'stretch_factor': number(),
            'max_shrink_factor': number(),
            'jpeg_quality': number(),
            'formats': {
                anything(): image_opts,
            },
            'font_dir': str(),
            'merge_method': str(),
        },
        'http': combined(
            http_opts,
            {
                'access_control_allow_origin': one_of(str(), {}),
            }
        ),
        'cache': {
            'base_dir': str(),
            'lock_dir': str(),
            'tile_lock_dir': str(),
            'directory_permissions': str(),
            'file_permissions': str(),
            'meta_size': [number()],
            'meta_buffer': number(),
            'bulk_meta_tiles': bool(),
            'max_tile_limit': number(),
            'minimize_meta_requests': bool(),
            'concurrent_tile_creators': int(),
            'link_single_color_images': one_of(bool(), 'symlink', 'hardlink'),
            's3': {
                'bucket_name': str(),
                'profile_name': str(),
                'region_name': str(),
                'endpoint_url': str(),
            },
            'azureblob': {
                'connection_string': str(),
                'container_name': str(),
            },
        },
        'grid': {
            'tile_size': [int()],
        },
        'srs': {
            'axis_order_ne': [str()],
            'axis_order_en': [str()],
            'proj_data_dir': str(),
            'preferred_src_proj': {anything(): [str()]},
        },
        'tiles': {
            'expires_hours': number(),
        },
        'mapserver': mapserver_opts,
        'renderd': {
            'address': str(),
        }
    },
    'grids': {
        anything(): grid_opts,
    },
    'caches': {
        anything(): {
            required('sources'): one_of([str], band_sources),
            'name': str(),
            'grids': [str()],
            'cache_dir': str(),
            'meta_size': [number()],
            'meta_buffer': number(),
            'bulk_meta_tiles': bool(),
            'minimize_meta_requests': bool(),
            'concurrent_tile_creators': int(),
            'disable_storage': bool(),
            'format': str(),
            'image': image_opts,
            'request_format': str(),
            'use_direct_from_level': number(),
            'use_direct_from_res': number(),
            'link_single_color_images': one_of(bool(), 'symlink', 'hardlink'),
            'cache_rescaled_tiles': bool(),
            'upscale_tiles': int(),
            'downscale_tiles': int(),
            'refresh_before': time_spec,
            'watermark': {
                'text': str,
                'font_size': number(),
                'color': one_of(str(), [number()]),
                'opacity': number(),
                'spacing': str(),
            },
            'cache': type_spec('type', cache_types)
        }
    },
    'services': {
        'demo': {},
        'kml': {
            'use_grid_names': bool(),
        },
        'tms': {
            'use_grid_names': bool(),
            'origin': str(),
        },
        'wmts': {
            'kvp': bool(),
            'restful': bool(),
            'restful_template': str(),
            'restful_featureinfo_template': str(),
            'md': ogc_service_md,
            'featureinfo_formats': [
                {
                    required('mimetype'): str(),
                    'suffix': str(),
                },
            ],
        },
        'wms': {
            'srs': [str()],
            'bbox_srs': [one_of(str(), {'bbox': [number()], 'srs': str()})],
            'image_formats': [str()],
            'attribution': {
                'text': str,
            },
            'featureinfo_types': [str()],
            'featureinfo_xslt': {
                anything(): str()
            },
            'on_source_errors': str(),
            'max_output_pixels': one_of(number(), [number()]),
            'strict': bool(),
            'md': ogc_service_md,
            'inspire_md': type_spec('type', inspire_md),
            'versions': [str()],
        },
    },

    'sources': {
        anything(): type_spec('type', {
            'wms': combined(source_commons, {
                'wms_opts': {
                    'version': str(),
                    'map': bool(),
                    'featureinfo': bool(),
                    'legendgraphic': bool(),
                    'legendurl': str(),
                    'featureinfo_format': str(),
                    'featureinfo_xslt': str(),
                    'featureinfo_out_format': str(),
                },
                'image': combined(image_opts, {
                    'opacity': number(),
                    'transparent_color': one_of(str(), [number()]),
                    'transparent_color_tolerance': number(),
                }),
                'supported_formats': [str()],
                'supported_srs': [str()],
                'http': http_opts,
                'on_error': on_error,
                'forward_req_params': [str()],
                required('req'): {
                    required('url'): str(),
                    anything(): anything()
                }
            }),
            'mapserver': combined(source_commons, {
                'wms_opts': {
                    'version': str(),
                    'map': bool(),
                    'featureinfo': bool(),
                    'legendgraphic': bool(),
                    'legendurl': str(),
                    'featureinfo_format': str(),
                    'featureinfo_xslt': str(),
                },
                'image': combined(image_opts, {
                    'opacity': number(),
                    'transparent_color': one_of(str(), [number()]),
                    'transparent_color_tolerance': number(),
                }),
                'supported_formats': [str()],
                'supported_srs': [str()],
                'forward_req_params': [str()],
                required('req'): {
                    required('map'): str(),
                    anything(): anything()
                },
                'mapserver': mapserver_opts,
            }),
            'tile': combined(source_commons, {
                required('url'): str(),
                'transparent': bool(),
                'image': image_opts,
                'grid': str(),
                'request_format': str(),
                'origin': str(),  # TODO: remove with 1.5
                'http': http_opts,
                'on_error': on_error,
            }),
            'mapnik': combined(source_commons, {
                required('mapfile'): str(),
                'transparent': bool(),
                'image': image_opts,
                'layers': one_of(str(), [str()]),
                'use_mapnik2': bool(),
                'scale_factor': number(),
                'multithreaded': bool(),
            }),
            'arcgis': combined(source_commons, {
                required('req'): {
                    required('url'): str(),
                    'dpi': int(),
                    'layers': str(),
                    'transparent': bool(),
                    'time': str()
                },
                'opts': {
                    'featureinfo': bool(),
                    'featureinfo_tolerance': number(),
                    'featureinfo_return_geometries': bool(),
                },
                'supported_srs': [str()],
                'http': http_opts,
                'on_error': on_error
            }),
            'debug': {
            },
        })
    },

    'layers': one_of(
        {
            anything(): combined(scale_hints, {
                'sources': [str],
                required('title'): str,
                'legendurl': str(),
                'md': wms_130_layer_md,
            })
        },
        recursive([combined(scale_hints, {
            'sources': [str],
            'tile_sources': [str],
            'name': str(),
            required('title'): str,
            'legendurl': str(),
            'wmts_rest_legendurl': str(),
            'wmts_kvp_legendurl': str(),
            'layers': recursive(),
            'md': wms_130_layer_md,
            'dimensions': {
                anything(): {
                    required('values'): [one_of(str, float, int)],
                    'default': one_of(str, float, int),
                }
            }
        })])
    ),
    # `parts` can be used for partial configurations that are referenced
    # from other sections (e.g. coverages, dimensions, etc.)
    'parts': anything(),
}


def add_source_to_mapproxy_yaml_spec(source_name, source_spec):
    """ Add a new source type to mapproxy_yaml_spec.
        Used by plugins.
    """

    # sources has a single anything() : {} member
    values = list(mapproxy_yaml_spec['sources'].values())
    assert len(values) == 1
    values[0].add_subspec(source_name, source_spec)


def add_service_to_mapproxy_yaml_spec(service_name, service_spec):
    """ Add a new service type to mapproxy_yaml_spec.
        Used by plugins.
    """

    mapproxy_yaml_spec['services'][service_name] = service_spec


def add_subcategory_to_layer_md(category_name, category_def):
    """ Add a new category to wms_130_layer_md.
        Used by plugins
    """
    wms_130_layer_md[category_name] = category_def
