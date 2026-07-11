import os
import foxesscloud.foxesscloud as fox

# Set credentials
fox.username = os.environ.get("FOXESS_USER")
fox.password = os.environ.get("FOXESS_PASS")

# Login
if not fox.get_token():
    print("Failed to login. Check credentials.")
    exit(1)

# Fetch available variables
variables = fox.get_vars()
print("Available variables:")
for v in variables:
    print(f"- {v['variable']} ({v['name']})")
