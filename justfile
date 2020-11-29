main := "aloh.py"
deps := "requirements.txt"
env := "env1"

# install dependencies
pip-install:
  pip install {{deps}}

# start environment
env-start:
  call {{env}}\Scripts\activate.bat

# install package locally
setup-dev:
  pip install -e .

# black and isort
lint:
  black .
  isort {{main}}

# start docs server
serve:
  mkdocs serve

# start Jupyter lab in examples
lab:
  jupyter notebook --notebook-dir="examples"

# run all examples
examples-all:
  python examples/example0.py
  python examples/example1a.py
  python examples/example2.py