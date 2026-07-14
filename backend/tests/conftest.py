import os


# Starlette's TestClient uses the synthetic Host header ``testserver``. Keep
# that authority unavailable in real launches while permitting it in tests.
os.environ["OKX_TEST_MODE"] = "1"
