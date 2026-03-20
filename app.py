from flask import Flask, request
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError
from datetime import datetime
import requests
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)

# ===============================
# WHATSAPP CLOUD API CONFIG
# ===============================
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

# ===============================
# MOCK DATABASE CLASSES
# ===============================
class MockDB:
    def __init__(self):
        self.users_data = {}
        self.orders_data = []
        self.restaurants_data = [
            {"_id": "1", "name": "Pizza Palace", "isOpen": True},
            {"_id": "2", "name": "Burger Barn", "isOpen": True},
        ]
        self.menu_data = [
            {"_id": "m1", "restaurant_id": "1", "name": "Margherita Pizza", "price": 12, "available": True},
            {"_id": "m2", "restaurant_id": "1", "name": "Pepperoni Pizza", "price": 14, "available": True},
            {"_id": "m3", "restaurant_id": "2", "name": "Classic Burger", "price": 8, "available": True},
        ]
    
    def find_one(self, query):
        if "number" in query:
            return self.users_data.get(query["number"])
        return None
    
    def insert_one(self, data):
        if "number" in data:
            self.users_data[data["number"]] = data
    
    def update_one(self, query, update):
        if "$set" in update:
            num = query.get("number")
            if num in self.users_data:
                self.users_data[num].update(update["$set"])
        elif "$push" in update:
            num = query.get("number")
            if num in self.users_data and "cart" in self.users_data[num]:
                self.users_data[num]["cart"].append(update["$push"]["cart"])
    
    def find(self, query):
        if query.get("isOpen"):
            return self.restaurants_data
        if "restaurant_id" in query:
            return [m for m in self.menu_data if m["restaurant_id"] == query["restaurant_id"] and m.get("available")]
        return []

class MockCollection:
    def __init__(self, items):
        self.items = items
    def find_one(self, query, sort=None):
        for item in self.items:
            if all(item.get(k) == v for k, v in query.items()):
                return item
        return None
    def insert_one(self, data):
        self.items.append(data)
    def find(self, query):
        return [i for i in self.items if all(i.get(k) == v for k, v in query.items())]

# ===============================
# DATABASE CONNECTION
# ===============================
db = None
USE_MOCK = False
mock_db = MockDB()

try:
    cluster = MongoClient(
        "mongodb+srv://foodchat_admin_05:Arjun%402035@foodchat.eidkjk7.mongodb.net/Restaurant?retryWrites=true&w=majority",
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=10000,
        socketTimeoutMS=10000,
        tls=True,
        tlsAllowInvalidCertificates=True,
        tlsAllowInvalidHostnames=True
    )
    cluster.admin.command('ping')
    db = cluster["Restaurant"]
    print("✅ MongoDB Connected Successfully")
except Exception as e:
    print(f"⚠️ MongoDB Connection Failed: {e}")
    print("⚠️ Switching to mock database mode...")
    USE_MOCK = True

if not USE_MOCK:
    users = db["users"]
    orders = db["orders"]
    menu = db["menu"]
    restaurants = db["restaurants"]
else:
    users = mock_db
    orders = MockCollection(mock_db.orders_data)
    menu = MockCollection(mock_db.menu_data)
    restaurants = MockCollection(mock_db.restaurants_data)

# ===============================
# SEND MESSAGE FUNCTION
# ===============================
def send_whatsapp_message(to, message):
    try:
        url = f"https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/messages"
        print(f"🔗 URL: {url}")
        print(f"📱 Sending to: {to}")
        print(f"💬 Message: {message}")

        headers = {
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": message}
        }

        print(f"📤 Payload: {payload}")
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"✅ Message Response: Status={r.status_code}, Body={r.text}")
        return r
    except Exception as e:
        print(f"❌ Error sending message to {to}: {e}")
        import traceback
        traceback.print_exc()
        return None


# ===============================
# WEBHOOK VERIFICATION
# ===============================
@app.route("/webhook", methods=["GET"])
def verify():

    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200

    return "Verification failed", 403


# ===============================
# WEBHOOK MESSAGE RECEIVER
# ===============================
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        print("Incoming:", data)

        try:
            value = data["entry"][0]["changes"][0]["value"]

            # ignore delivery status updates
            if "messages" not in value:
                return "OK", 200

            message = value["messages"][0]
            number = message["from"]

            if message["type"] != "text":
                send_whatsapp_message(number, "Please send text messages only.")
                return "OK", 200

            text = message["text"]["body"].strip().lower()

        except Exception as e:
            print("Parsing Error:", e)
            return "OK", 200

        user = users.find_one({"number": number})
        print(f"User lookup for {number}: {user}")

        # ===============================
        # NEW USER
        # ===============================
        if not user:

            users.insert_one({
                "number": number,
                "status": "main",
                "cart": [],
                "restaurant_id": None,
                "created_at": datetime.now()
            })

            send_whatsapp_message(
                number,
                "Welcome to FoodChat 🍽\n\n"
                "1️⃣ View Restaurants\n"
                "2️⃣ View Order Status\n\n"
                "Reply with option number."
            )

            return "OK", 200

        # reset if user says hi
        if text in ["hi", "hello", "start"]:
            users.update_one(
                {"number": number},
                {"$set": {"status": "main", "cart": []}}
            )

            send_whatsapp_message(
                number,
                "Welcome back 🍽\n\n"
                "1️⃣ View Restaurants\n"
                "2️⃣ View Order Status"
            )

            return "OK", 200

        # ===============================
        # MAIN MENU
        # ===============================
        if user["status"] == "main":

            if text == "1":

                restaurant_list = list(restaurants.find({"isOpen": True}))

                msg = "🏬 Available Restaurants\n\n"

                for i, r in enumerate(restaurant_list):
                    msg += f"{i+1}. {r['name']}\n"

                msg += "\nReply with restaurant number."

                users.update_one(
                    {"number": number},
                    {"$set": {"status": "select_restaurant"}}
                )

                send_whatsapp_message(number, msg)

            elif text == "2":

                last_order = orders.find_one(
                    {"number": number},
                    sort=[("order_time", -1)]
                )

                if last_order:
                    send_whatsapp_message(
                        number,
                        f"Your last order status: {last_order['status']}"
                    )
                else:
                    send_whatsapp_message(number, "No previous orders found.")

            else:
                send_whatsapp_message(number, "Please enter 1 or 2.")

        # ===============================
        # SELECT RESTAURANT
        # ===============================
        elif user["status"] == "select_restaurant":

            try:
                option = int(text)
            except:
                send_whatsapp_message(number, "Please enter a valid number.")
                return "OK", 200

            restaurant_list = list(restaurants.find({"isOpen": True}))

            if option < 1 or option > len(restaurant_list):
                send_whatsapp_message(number, "Invalid selection.")
                return "OK", 200

            selected = restaurant_list[option - 1]

            users.update_one(
                {"number": number},
                {"$set": {
                    "status": "ordering",
                    "restaurant_id": selected["_id"],
                    "cart": []
                }}
            )

            menu_items = list(menu.find({
                "restaurant_id": selected["_id"],
                "available": True
            }))

            msg = f"📋 {selected['name']} Menu\n\n"

            for i, item in enumerate(menu_items):
                msg += f"{i+1}. {item['name']} - ₹{item['price']}\n"

            msg += "\nReply with item number or type DONE."

            send_whatsapp_message(number, msg)

        # ===============================
        # ORDERING MODE
        # ===============================
        elif user["status"] == "ordering":

            if text == "done":

                user = users.find_one({"number": number})

                if not user.get("cart"):
                    send_whatsapp_message(number, "Cart empty.")
                    return "OK", 200

                total = sum(i["price"] for i in user["cart"])

                users.update_one(
                    {"number": number},
                    {"$set": {"status": "address", "bill": total}}
                )

                send_whatsapp_message(number, f"Total: ₹{total}")
                send_whatsapp_message(number, "Enter delivery address.")

            else:

                try:
                    option = int(text)
                except:
                    send_whatsapp_message(number, "Enter valid item number.")
                    return "OK", 200

                menu_items = list(menu.find({
                    "restaurant_id": user["restaurant_id"],
                    "available": True
                }))

                if option < 1 or option > len(menu_items):
                    send_whatsapp_message(number, "Invalid item.")
                    return "OK", 200

                item = menu_items[option - 1]

                users.update_one(
                    {"number": number},
                    {"$push": {
                        "cart": {
                            "name": item["name"],
                            "price": item["price"]
                        }
                    }}
                )

                send_whatsapp_message(number, f"Added {item['name']}")

        # ===============================
        # ADDRESS STEP
        # ===============================
        elif user["status"] == "address":

            orders.insert_one({
                "number": number,
                "restaurant_id": user["restaurant_id"],
                "items": user["cart"],
                "bill": user["bill"],
                "address": text,
                "status": "pending",
                "order_time": datetime.now()
            })

            users.update_one(
                {"number": number},
                {"$set": {"status": "main", "cart": []}}
            )

            send_whatsapp_message(number, "✅ Order placed successfully!")

        return "OK", 200

    except Exception as e:
        print(f"Webhook Error: {e}")
        import traceback
        traceback.print_exc()
        return "OK", 200


if __name__ == "__main__":
    app.run(debug=True)