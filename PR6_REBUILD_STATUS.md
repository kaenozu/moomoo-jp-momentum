# PR #6 rebuild diagnostics

- bootstrap: 0
- compile: 0
- script: 1
- install: 99
- pytest: 99
- ruff: 99
- pyright: 99
- cli: 99
- artifacts: 99

## bootstrap
```text
Collecting pyyaml
  Using cached pyyaml-6.0.3-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl.metadata (2.4 kB)
Using cached pyyaml-6.0.3-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (806 kB)
Installing collected packages: pyyaml
Successfully installed pyyaml-6.0.3
```

## compile
```text
```

## git
```text
From https://github.com/kaenozu/moomoo-jp-momentum
 * branch            master     -> FETCH_HEAD
HEAD is now at 628395b fix: make virtual order reservations fill-safe (#5)
```

## script
```text
Traceback (most recent call last):
  File "/tmp/rebuild_pr6.py", line 493, in <module>
    Path("docs/known_limitations.md").write_text(limitations, encoding="utf-8")
  File "/opt/hostedtoolcache/Python/3.11.15/x64/lib/python3.11/pathlib.py", line 1078, in write_text
    with self.open(mode='w', encoding=encoding, errors=errors, newline=newline) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.11.15/x64/lib/python3.11/pathlib.py", line 1044, in open
    return io.open(self, mode, buffering, encoding, errors, newline)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
FileNotFoundError: [Errno 2] No such file or directory: 'docs/known_limitations.md'
```

## install
```text
Skipped because rebuild script failed
```

## pytest
```text
Skipped
```

## ruff
```text
Skipped
```

## pyright
```text
Skipped
```

## cli
```text
Skipped
```

## artifacts
```text
Skipped
```
