"""Contract tests opt into fixture data before importing the HTTP application."""

import os

os.environ.setdefault("DATA_MODE", "synthetic")
