Dev Setup
=========

* Create parent directory for source, applications and the virtual env
* Clone source into directory mapproxy: `git clone`
* Install dependencies: <https://mapproxy.org/docs/latest/install.html#install-dependencies>
* Create virtualenv: `python3.12 -m venv ./venv`
* Activate virtualenv: `source venv/bin/activate`
* Install mapproxy: `pip install -e mapproxy/`
* Install dev dependencies: `pip install -r mapproxy/requirements-tests.txt`
* Run tests:
  * `cd mapproxy`
  * `pytest mapproxy`
  * Run single test: `pytest mapproxy/test/unit/test_grid.py -v`
* Create an application: `mapproxy-util create -t base-config apps/base`

* Start a dev server in debug mode: `mapproxy-util serve-develop apps/base/mapproxy.yaml --debug`

Coding Style
------------

PEP8: <https://www.python.org/dev/peps/pep-0008/>

PyCharm / Intellij
------------------

* Mark the mapproxy folder as `Sources` 
* Set your venv as the project SDK / Python interpreter in your project settings
* Add the root folder of mapproxy to the paths of the python interpreter in your project settings


Debugging
---------

* With Intellij
  * Create run configuration
  * Select script `mapproxy-util` from the `bin` folder in your venv folder.
  * Add script parameters `serve-develop apps/base/mapproxy.yaml`.
  * Start configuration in debug mode, no need to start mapproxy in debug mode.

* With PyCharm:
  * Attach to dev server with <https://www.jetbrains.com/help/pycharm/attaching-to-local-process.html>

* With ipython:
  * `pip install ipython ipdb`

* With Visual Studio Code:
  * After creating a virtual env and mapproxy configuration:
  * Create a `launch.json` file in the project-root/.vscode directory with the following content:

    ```json
    {
      "version": "0.2.0",
      "configurations": [
        {
          "name": "Debug local mapproxy",
          "type": "python",
          "request": "launch",
          "program": ".venv/bin/mapproxy-util",
          "args": ["serve-develop", "-b", ":1234", "config/mapproxy.yaml"],
          "console": "integratedTerminal",
          "autoReload": {
            "enable": true
          }
        }
      ]
    }
    ```

  * Then start debugging by hitting `F5`.

Some more details in the documentation
--------------------------------------

See <https://mapproxy.org/docs/latest/development.html>

Some incomplete notes about the structure of the software
---------------------------------------------------------

A mapproxy app decides on the request-URL which handler it starts. There exist different handlers (services) for WMS, WMTS.

Incoming http requests are transformed into own request objects (for example `WMSRequest`).

The class `TileManager` decides if tiles are served from cache or from a source.

All caches need to implement the interface `TileCacheBase`.

The code in `config/` builds mapproxy out of a configuration. `config/spec.py` validates the config.

The sources live in `source/` which in turn use low-level functions from `client/` to request the data.

The file `layer.py` merges/clips/transforms tiles.

The whole of MapProxy is stateless apart from the cache which uses locks on file system level.
