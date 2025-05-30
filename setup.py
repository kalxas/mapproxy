import platform
import importlib.metadata

from setuptools import setup, find_packages


install_requires = [
    'PyYAML>=3.0',
    'future',
    'pyproj>=2',
    'jsonschema>=4',
    'werkzeug<4'
]


def package_installed(pkg):
    """Check if package is installed"""
    try:
        importlib.metadata.version(pkg)
    except importlib.metadata.PackageNotFoundError:
        return False
    else:
        return True


# depend on Pillow if it is installed, otherwise
# depend on PIL if it is installed, otherwise
# require Pillow
if package_installed('Pillow'):
    install_requires.append('Pillow !=2.4.0,!=8.3.0,!=8.3.1')
elif package_installed('PIL'):
    install_requires.append('PIL>=1.1.6,<1.2.99')
else:
    install_requires.append('Pillow !=2.4.0,!=8.3.0,!=8.3.1')

if platform.python_version_tuple() < ('2', '6'):
    # for mapproxy-seed
    install_requires.append('multiprocessing>=2.6')


def long_description(changelog_releases=10):
    import re
    import textwrap

    readme = open('README.md').read()
    changes = ['Changes\n-------\n']
    version_line_re = re.compile(r'^\d\.\d+\.\d+\S*\s20\d\d-\d\d-\d\d')
    for line in open('CHANGES.txt'):
        if version_line_re.match(line):
            if changelog_releases == 0:
                break
            changelog_releases -= 1
        changes.append(line)

    changes.append(textwrap.dedent('''
        Older changes
        -------------
        See https://raw.github.com/mapproxy/mapproxy/master/CHANGES.txt
        '''))
    return readme + ''.join(changes)


setup(
    name='MapProxy',
    version="4.1.1",
    description='An accelerating proxy for tile and web map services',
    long_description=long_description(7),
    long_description_content_type='text/x-rst',
    author='Oliver Tonnhofer',
    author_email='olt@omniscale.de',
    maintainer='terrestris GmbH & Co. KG',
    maintainer_email='info@terrestris.de',
    url='https://mapproxy.org',
    license='Apache Software License 2.0',
    packages=find_packages(),
    include_package_data=True,
    entry_points={
        'console_scripts': [
            'mapproxy-seed = mapproxy.seed.script:main',
            'mapproxy-util = mapproxy.script.util:main',
        ],
    },
    package_data={'': ['*.xml', '*.yaml', '*.ttf', '*.wsgi', '*.ini', '*.json']},
    install_requires=install_requires,
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Internet :: Proxy Servers",
        "Topic :: Internet :: WWW/HTTP :: WSGI",
        "Topic :: Scientific/Engineering :: GIS",
    ],
    zip_safe=False
)
