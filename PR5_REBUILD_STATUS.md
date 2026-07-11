# PR #5 rebuild diagnostics

- script: 0
- install: 0
- pytest: 1
- ruff: 1
- pyright: 0
- dry-run: 0
- artifacts: 0

## script
```text
```

## install
```text
Collecting PyCryptodome (from futu-api>=9.1.0->-r requirements.txt (line 5))
  Downloading pycryptodome-3.23.0-cp37-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl.metadata (3.4 kB)
Collecting protobuf>=3.20.0 (from futu-api>=9.1.0->-r requirements.txt (line 5))
  Downloading protobuf-7.35.1-cp310-abi3-manylinux2014_x86_64.whl.metadata (595 bytes)
Collecting simplejson (from futu-api>=9.1.0->-r requirements.txt (line 5))
  Downloading simplejson-4.1.1-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl.metadata (3.8 kB)
Collecting numpy>=1.26.0 (from pandas>=2.0.0->-r requirements.txt (line 8))
  Downloading numpy-2.5.1-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl.metadata (6.6 kB)
Requirement already satisfied: python-dateutil>=2.8.2 in /usr/lib/python3/dist-packages (from pandas>=2.0.0->-r requirements.txt (line 8)) (2.8.2)
Collecting tzlocal>=3.0 (from apscheduler>=3.10.0->-r requirements.txt (line 14))
  Downloading tzlocal-5.4.4-py3-none-any.whl.metadata (7.7 kB)
Collecting altair!=5.4.0,!=5.4.1,<7,>=4.0 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading altair-6.2.2-py3-none-any.whl.metadata (11 kB)
Requirement already satisfied: blinker<2,>=1.5.0 in /usr/lib/python3/dist-packages (from streamlit>=1.30.0->-r requirements.txt (line 17)) (1.7.0)
Collecting cachetools<8,>=5.5 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading cachetools-7.1.4-py3-none-any.whl.metadata (5.5 kB)
Requirement already satisfied: click<9,>=7.0 in /usr/lib/python3/dist-packages (from streamlit>=1.30.0->-r requirements.txt (line 17)) (8.1.6)
Collecting gitpython!=3.1.19,<4,>=3.0.7 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading gitpython-3.1.50-py3-none-any.whl.metadata (14 kB)
Requirement already satisfied: packaging>=20 in /usr/lib/python3/dist-packages (from streamlit>=1.30.0->-r requirements.txt (line 17)) (24.0)
Collecting pillow<13,>=7.1.0 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading pillow-12.3.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl.metadata (9.1 kB)
Collecting pydeck<1,>=0.8.0b4 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading pydeck-0.9.3-py2.py3-none-any.whl.metadata (4.2 kB)
Collecting pyarrow>=7.0 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading pyarrow-25.0.0-cp312-cp312-manylinux_2_28_x86_64.whl.metadata (3.0 kB)
Collecting tenacity<10,>=8.1.0 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading tenacity-9.1.4-py3-none-any.whl.metadata (1.2 kB)
Collecting toml<2,>=0.10.1 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading toml-0.10.2-py2.py3-none-any.whl.metadata (7.1 kB)
Requirement already satisfied: typing-extensions<5,>=4.10.0 in /usr/lib/python3/dist-packages (from streamlit>=1.30.0->-r requirements.txt (line 17)) (4.10.0)
Collecting starlette>=0.40.0 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading starlette-1.3.1-py3-none-any.whl.metadata (6.4 kB)
Collecting uvicorn>=0.30.0 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading uvicorn-0.51.0-py3-none-any.whl.metadata (6.6 kB)
Collecting httptools>=0.6.3 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading httptools-0.8.0-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl.metadata (3.5 kB)
Collecting anyio>=4.0.0 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading anyio-4.14.1-py3-none-any.whl.metadata (4.6 kB)
Collecting python-multipart>=0.0.10 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading python_multipart-0.0.32-py3-none-any.whl.metadata (2.1 kB)
Collecting websockets>=12.0.0 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading websockets-16.1-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl.metadata (6.8 kB)
Collecting itsdangerous>=2.1.2 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading itsdangerous-2.2.0-py3-none-any.whl.metadata (1.9 kB)
Collecting watchdog<7,>=2.1.5 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading watchdog-6.0.0-py3-none-manylinux2014_x86_64.whl.metadata (44 kB)
     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 44.3/44.3 kB 14.2 MB/s eta 0:00:00
Collecting contourpy>=1.0.1 (from matplotlib>=3.7.0->-r requirements.txt (line 23))
  Downloading contourpy-1.3.3-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl.metadata (5.5 kB)
Collecting cycler>=0.10 (from matplotlib>=3.7.0->-r requirements.txt (line 23))
  Downloading cycler-0.12.1-py3-none-any.whl.metadata (3.8 kB)
Collecting fonttools>=4.22.0 (from matplotlib>=3.7.0->-r requirements.txt (line 23))
  Downloading fonttools-4.63.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl.metadata (118 kB)
     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 118.7/118.7 kB 19.8 MB/s eta 0:00:00
Collecting kiwisolver>=1.3.1 (from matplotlib>=3.7.0->-r requirements.txt (line 23))
  Downloading kiwisolver-1.5.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl.metadata (5.1 kB)
Requirement already satisfied: pyparsing>=3 in /usr/lib/python3/dist-packages (from matplotlib>=3.7.0->-r requirements.txt (line 23)) (3.1.1)
Collecting iniconfig>=1.0.1 (from pytest>=9.0.0->-r requirements-dev.txt (line 5))
  Downloading iniconfig-2.3.0-py3-none-any.whl.metadata (2.5 kB)
Collecting pluggy<2,>=1.5 (from pytest>=9.0.0->-r requirements-dev.txt (line 5))
  Downloading pluggy-1.6.0-py3-none-any.whl.metadata (4.8 kB)
Requirement already satisfied: pygments>=2.7.2 in /usr/lib/python3/dist-packages (from pytest>=9.0.0->-r requirements-dev.txt (line 5)) (2.17.2)
Collecting nodeenv>=1.6.0 (from pyright>=1.1.400->-r requirements-dev.txt (line 10))
  Downloading nodeenv-1.10.0-py2.py3-none-any.whl.metadata (24 kB)
Requirement already satisfied: urllib3>=2 in /usr/lib/python3/dist-packages (from types-requests>=2.32.0->-r requirements-dev.txt (line 14)) (2.0.7)
Requirement already satisfied: jsonschema>=3.0 in /usr/lib/python3/dist-packages (from altair!=5.4.0,!=5.4.1,<7,>=4.0->streamlit>=1.30.0->-r requirements.txt (line 17)) (4.10.3)
Collecting narwhals>=2.4.0 (from altair!=5.4.0,!=5.4.1,<7,>=4.0->streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading narwhals-2.23.0-py3-none-any.whl.metadata (15 kB)
Collecting typing-extensions<5,>=4.10.0 (from streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading typing_extensions-4.16.0-py3-none-any.whl.metadata (3.3 kB)
Requirement already satisfied: idna>=2.8 in /usr/lib/python3/dist-packages (from anyio>=4.0.0->streamlit>=1.30.0->-r requirements.txt (line 17)) (3.6)
Collecting gitdb<5,>=4.0.1 (from gitpython!=3.1.19,<4,>=3.0.7->streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading gitdb-4.0.12-py3-none-any.whl.metadata (1.2 kB)
Collecting h11>=0.8 (from uvicorn>=0.30.0->streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading h11-0.16.0-py3-none-any.whl.metadata (8.3 kB)
Collecting smmap<6,>=3.0.1 (from gitdb<5,>=4.0.1->gitpython!=3.1.19,<4,>=3.0.7->streamlit>=1.30.0->-r requirements.txt (line 17))
  Downloading smmap-5.0.3-py3-none-any.whl.metadata (4.6 kB)
Requirement already satisfied: attrs>=17.4.0 in /usr/lib/python3/dist-packages (from jsonschema>=3.0->altair!=5.4.0,!=5.4.1,<7,>=4.0->streamlit>=1.30.0->-r requirements.txt (line 17)) (23.2.0)
Requirement already satisfied: pyrsistent!=0.17.0,!=0.17.1,!=0.17.2,>=0.14.0 in /usr/lib/python3/dist-packages (from jsonschema>=3.0->altair!=5.4.0,!=5.4.1,<7,>=4.0->streamlit>=1.30.0->-r requirements.txt (line 17)) (0.20.0)
Downloading pandas-3.0.3-cp312-cp312-manylinux_2_24_x86_64.manylinux_2_28_x86_64.whl (10.9 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 10.9/10.9 MB 153.7 MB/s eta 0:00:00
Downloading apscheduler-3.11.3-py3-none-any.whl (66 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 66.0/66.0 kB 23.0 MB/s eta 0:00:00
Downloading streamlit-1.59.1-py3-none-any.whl (10.3 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 10.3/10.3 MB 163.2 MB/s eta 0:00:00
Downloading matplotlib-3.11.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl (10.0 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 10.0/10.0 MB 155.3 MB/s eta 0:00:00
Downloading python_dotenv-1.2.2-py3-none-any.whl (22 kB)
Downloading pytest-9.1.1-py3-none-any.whl (386 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 386.5/386.5 kB 101.1 MB/s eta 0:00:00
Downloading pytest_mock-3.15.1-py3-none-any.whl (10 kB)
Downloading ruff-0.15.21-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (11.5 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 11.5/11.5 MB 147.5 MB/s eta 0:00:00
Downloading pyright-1.1.411-py3-none-any.whl (6.2 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 6.2/6.2 MB 158.3 MB/s eta 0:00:00
Downloading types_pyyaml-6.0.12.20260518-py3-none-any.whl (20 kB)
Downloading types_requests-2.33.0.20260518-py3-none-any.whl (21 kB)
Downloading altair-6.2.2-py3-none-any.whl (797 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 797.6/797.6 kB 127.5 MB/s eta 0:00:00
Downloading anyio-4.14.1-py3-none-any.whl (124 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 124.9/124.9 kB 37.7 MB/s eta 0:00:00
Downloading cachetools-7.1.4-py3-none-any.whl (16 kB)
Downloading contourpy-1.3.3-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl (362 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 362.6/362.6 kB 95.0 MB/s eta 0:00:00
Downloading cycler-0.12.1-py3-none-any.whl (8.3 kB)
Downloading fonttools-4.63.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl (5.0 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 5.0/5.0 MB 166.5 MB/s eta 0:00:00
Downloading gitpython-3.1.50-py3-none-any.whl (212 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 212.5/212.5 kB 60.0 MB/s eta 0:00:00
Downloading httptools-0.8.0-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl (523 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 523.9/523.9 kB 111.9 MB/s eta 0:00:00
Downloading iniconfig-2.3.0-py3-none-any.whl (7.5 kB)
Downloading itsdangerous-2.2.0-py3-none-any.whl (16 kB)
Downloading kiwisolver-1.5.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl (1.5 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 1.5/1.5 MB 143.3 MB/s eta 0:00:00
Downloading nodeenv-1.10.0-py2.py3-none-any.whl (23 kB)
Downloading numpy-2.5.1-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl (16.7 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 16.7/16.7 MB 145.8 MB/s eta 0:00:00
Downloading pillow-12.3.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl (6.9 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 6.9/6.9 MB 157.1 MB/s eta 0:00:00
Downloading pluggy-1.6.0-py3-none-any.whl (20 kB)
Downloading protobuf-7.35.1-cp310-abi3-manylinux2014_x86_64.whl (327 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 327.1/327.1 kB 87.8 MB/s eta 0:00:00
Downloading pyarrow-25.0.0-cp312-cp312-manylinux_2_28_x86_64.whl (50.1 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 50.1/50.1 MB 98.8 MB/s eta 0:00:00
Downloading pydeck-0.9.3-py2.py3-none-any.whl (11.4 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 11.4/11.4 MB 161.4 MB/s eta 0:00:00
Downloading python_multipart-0.0.32-py3-none-any.whl (30 kB)
Downloading starlette-1.3.1-py3-none-any.whl (73 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 73.6/73.6 kB 26.8 MB/s eta 0:00:00
Downloading tenacity-9.1.4-py3-none-any.whl (28 kB)
Downloading toml-0.10.2-py2.py3-none-any.whl (16 kB)
Downloading typing_extensions-4.16.0-py3-none-any.whl (45 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 45.6/45.6 kB 14.9 MB/s eta 0:00:00
Downloading tzlocal-5.4.4-py3-none-any.whl (18 kB)
Downloading uvicorn-0.51.0-py3-none-any.whl (73 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 73.2/73.2 kB 23.9 MB/s eta 0:00:00
Downloading watchdog-6.0.0-py3-none-manylinux2014_x86_64.whl (79 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 79.1/79.1 kB 14.4 MB/s eta 0:00:00
Downloading websockets-16.1-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl (187 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 187.3/187.3 kB 62.5 MB/s eta 0:00:00
Downloading pycryptodome-3.23.0-cp37-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (2.3 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 2.3/2.3 MB 161.8 MB/s eta 0:00:00
Downloading simplejson-4.1.1-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl (190 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 190.3/190.3 kB 60.6 MB/s eta 0:00:00
Downloading gitdb-4.0.12-py3-none-any.whl (62 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 62.8/62.8 kB 22.6 MB/s eta 0:00:00
Downloading h11-0.16.0-py3-none-any.whl (37 kB)
Downloading narwhals-2.23.0-py3-none-any.whl (458 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 458.9/458.9 kB 111.3 MB/s eta 0:00:00
Downloading smmap-5.0.3-py3-none-any.whl (24 kB)
Building wheels for collected packages: futu-api
  Building wheel for futu-api (setup.py): started
  Building wheel for futu-api (setup.py): finished with status 'done'
  Created wheel for futu-api: filename=futu_api-10.8.6808-py3-none-any.whl size=6691452 sha256=2867a2c24d9eab572ed430bdae6fa60fc74db41ea7890138c041e22de9bf1f08
  Stored in directory: /home/runner/.cache/pip/wheels/8d/42/ad/c2d15147005efd4816eaf1ad0ebb31d8c1bf67d203c5663846
Successfully built futu-api
Installing collected packages: websockets, watchdog, tzlocal, typing-extensions, types-requests, types-pyyaml, toml, tenacity, smmap, simplejson, ruff, python-multipart, python-dotenv, PyCryptodome, pyarrow, protobuf, pluggy, pillow, numpy, nodeenv, narwhals, kiwisolver, itsdangerous, iniconfig, httptools, h11, fonttools, cycler, cachetools, uvicorn, pytest, pyright, pydeck, pandas, gitdb, contourpy, apscheduler, anyio, altair, starlette, pytest-mock, matplotlib, gitpython, futu-api, streamlit
Successfully installed PyCryptodome-3.23.0 altair-6.2.2 anyio-4.14.1 apscheduler-3.11.3 cachetools-7.1.4 contourpy-1.3.3 cycler-0.12.1 fonttools-4.63.0 futu-api-10.8.6808 gitdb-4.0.12 gitpython-3.1.50 h11-0.16.0 httptools-0.8.0 iniconfig-2.3.0 itsdangerous-2.2.0 kiwisolver-1.5.0 matplotlib-3.11.0 narwhals-2.23.0 nodeenv-1.10.0 numpy-2.5.1 pandas-3.0.3 pillow-12.3.0 pluggy-1.6.0 protobuf-7.35.1 pyarrow-25.0.0 pydeck-0.9.3 pyright-1.1.411 pytest-9.1.1 pytest-mock-3.15.1 python-dotenv-1.2.2 python-multipart-0.0.32 ruff-0.15.21 simplejson-4.1.1 smmap-5.0.3 starlette-1.3.1 streamlit-1.59.1 tenacity-9.1.4 toml-0.10.2 types-pyyaml-6.0.12.20260518 types-requests-2.33.0.20260518 typing-extensions-4.16.0 tzlocal-5.4.4 uvicorn-0.51.0 watchdog-6.0.0 websockets-16.1
```

## pytest
```text
..........................................F............................. [ 62%]
............................................                             [100%]
=================================== FAILURES ===================================
_______ TestPendingCashReservation.test_validate_buy_uses_available_cash _______

self = <test_core.TestPendingCashReservation object at 0x7f780ffbcd10>
tmp_path = PosixPath('/tmp/pytest-of-runner/pytest-0/test_validate_buy_uses_availab0')

    def test_validate_buy_uses_available_cash(self, tmp_path):
        """_validate_buy_orderがavailable cash(buffer込み)を使って判定すること"""
        db_path = tmp_path / "vtm_validate.db"
        config = Config("tests/fixtures/config.test.yaml")
        config._config["database"] = {"path": str(db_path)}
        DataStore(config)
    
        with sqlite3.connect(db_path) as conn:
            for code in ("JP.0001", "JP.0002"):
                conn.execute(
                    "INSERT INTO symbols (code, name, type, role, tradable, enabled) VALUES (?, ?, 'stock', 'trade_candidate', 1, 1)",
                    (code, f"テスト{code}"),
                )
                conn.execute(
                    "INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) VALUES (?, '2026-01-05', 1000, 1000, 1000, 1000, 10000, 10000000)",
                    (code,),
                )
            conn.execute(
                "INSERT INTO virtual_equity_curve (strategy_name, date, cash, position_value, total_equity, created_at) "
                "VALUES ('default', '2026-01-05', 15000, 0, 15000, '2026-01-05T00:00:00')"
            )
            # pending BUY: JP.0001の1000円 x 10株 = 10,000円 → buffer 2% で 10,200円予約
            conn.execute(
                "INSERT INTO virtual_orders (strategy_name, code, side, quantity, order_type, status, submitted_at, created_at, updated_at) "
                "VALUES ('default', 'JP.0001', 'BUY', 10, 'MARKET_SIM', 'PENDING', '2026-01-05 15:30:00', '2026-01-05T00:00:00', '2026-01-05T00:00:00')"
            )
    
        from src.virtual_trade import VirtualTradeManager
        vtm = VirtualTradeManager(config)
    
        # available cash = 15,000 - 10,200(buffer) = 4,800
        # JP.0002で8,000円の注文 → cash不足で拒否
        with vtm._get_connection() as conn:
            ok, reason = vtm._validate_buy_order(
                conn, "default", "JP.0002", 8, "MARKET_SIM", None, "2026-01-05",
            )
        assert not ok
        assert "不足" in reason
    
        # JP.0002で4,800円の注文 → OK
        with vtm._get_connection() as conn:
            ok, _ = vtm._validate_buy_order(
                conn, "default", "JP.0002", 4, "MARKET_SIM", None, "2026-01-05",
            )
>       assert ok
E       assert False

tests/test_core.py:692: AssertionError
=========================== short test summary info ============================
FAILED tests/test_core.py::TestPendingCashReservation::test_validate_buy_uses_available_cash - assert False
1 failed, 115 passed, 1 deselected in 27.17s
```

## ruff
```text
::error title=ruff (F401),file=/home/runner/work/moomoo-jp-momentum/moomoo-jp-momentum/tests/test_core.py,line=14,col=8,endLine=14,endColumn=14::tests/test_core.py:14:8: F401 `pytest` imported but unused%0A  help: Remove unused import: `pytest`
```

## pyright
```text
0 errors, 0 warnings, 0 informations
```

## dry-run
```text
2026-07-11 13:40:23 [INFO] 基準日: 2026-07-11, dry-run: True
============================================================
Moomoo 日次運用サイクル
============================================================

============================================================
日次サイクル結果
============================================================
  connection_attempted: False
  database_write_attempted: False
  virtual_trade_enabled: True
  symbols: 366
  benchmarks: 10
  所要時間: 0.0秒

[DONE] dry-run 完了
```
