import os
import sys

# Add src path to path for autodoc
sys.path.insert(0, os.path.abspath('../src'))

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'AMQCSLdb'
copyright = '2025, FieryIceStickie'
author = 'FieryIceStickie'
version = '1.0'
release = '1.0.6'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = ['sphinx.ext.napoleon', 'sphinx.ext.autodoc', 'sphinx_autodoc_typehints', 'sphinx_design']

templates_path = ['_templates']
exclude_patterns = []


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'furo'
html_static_path = ['_static']


rst_prolog = """
.. |AMQ| replace:: `AMQ <https://animemusicquiz.com/>`__
"""
autodoc_member_order = 'bysource'
