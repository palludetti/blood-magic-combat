import os
from dotenv import load_dotenv

# Explicit loading of .env for tests and local execution without overriding existing variables
load_dotenv(override=False)
