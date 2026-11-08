"""AI mock interview engine."""
import logging

# The application decides where log output goes; without this, warnings would also print to the candidate's terminal.
logging.getLogger(__name__).addHandler(logging.NullHandler())
