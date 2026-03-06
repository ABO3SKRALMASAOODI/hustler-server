import os
import requests

# Make sure your Paddle API key is set in the environment
PADDLE_API_KEY = os.getenv("PADDLE_API_KEY")

if not PADDLE_API_KEY:
    print("❌ PADDLE_API_KEY not found in environment variables.")
    exit(1)

# Ping the Paddle API with a basic GET request to a valid endpoint
url = "https://api.paddle.com/products"  # This endpoint lists products

headers = {
    "Authorization": f"Bearer {PADDLE_API_KEY}",
    "Content-Type": "application/json"
}

response = requests.get(url, headers=headers)

print("✅ Status Code:", response.status_code)
try:
    print("✅ Response JSON:", response.json())
except Exception as e:
    print("❌ Failed to parse JSON:", e)
    print("Raw text:", response.text)
