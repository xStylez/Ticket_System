from flask import Flask, jsonify, request, send_from_directory, session, render_template, redirect
from ticketdb import setup_database, get_connection
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import timedelta
import os

app = Flask(__name__)
app.secret_key = "dev-secret"
setup_database()

@app.route("/")
def home():
    return redirect("/login")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    username = request.form.get("username")
    password = request.form.get("password")
    name = request.form.get("name")

    if not username or not password or not name:
        return render_template("register.html", error="Username, password and name are required.")

    if len(password) < 10:
        return render_template("register.html", error="Password must be at least 10 characters.")

    if not any(character.isupper() for character in password):
        return render_template("register.html", error="Password must include a capital letter.")

    if not name.isalnum():
        return render_template("register.html", error="Name can not include special characters.")
    
    banned_path = os.path.join(os.path.dirname(__file__), "banned.txt")
    banned = []
    if os.path.exists(banned_path):
        with open(banned_path, encoding="utf-8") as banned_file:
            banned = [line.strip().lower() for line in banned_file if line.strip()]

    leet = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t"})
    plain_name = name.lower().translate(leet)

    if any(word in plain_name for word in banned):
        return render_template("register.html", error="That name is not allowed.")


    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("SELECT username FROM users WHERE username = ?", (username,))
    if cursor.fetchone():
        connection.close()
        return render_template("register.html", error="Username already exists.")

    cursor.execute("SELECT name FROM users WHERE name = ?", (name,))
    if cursor.fetchone():
        connection.close()
        return render_template("register.html", error="Name already exists.")

    password_hash = generate_password_hash(password)

    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("INSERT INTO users(name, username, password_hash) VALUES(?, ?, ?)", (name, username, password_hash))
        connection.commit()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        connection.close()
    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    username = request.form.get("username")
    password = request.form.get("password")

    if not username or not password:
        return render_template("login.html", error="Username and password are required.")

    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT id, password_hash, role FROM users where username = ?", (username,))
        user = cursor.fetchone()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        connection.close()
    
    if not user or not check_password_hash(user[1], password):
        return render_template("login.html", error="Invalid username or password.")

    session.permanent = True
    session["user_id"] = user[0]
    session["role"] = user[2]

    return redirect("/tickets")

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/login")

@app.route("/me", methods=["GET"])
def me():
    if not session.get("user_id"):
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"message": "User is logged in"}), 200


@app.route("/tickets/new", methods=["GET", "POST"])
def new_ticket():
    if not session.get("user_id"):
        return redirect("/login")
    
    if request.method == "POST":
        title = request.form.get("title")
        details = request.form.get("details")
        if title:
            connection = get_connection()
            cursor = connection.cursor()
            cursor.execute("INSERT INTO tickets(title, details, user_id) VALUES(?, ?, ?)", (title, details, session["user_id"]))
            connection.commit()
            connection.close()
        return redirect("/tickets")

    return render_template("new_ticket.html")


@app.route("/tickets", methods=["GET", "POST"])
def tickets():
    if not session.get("user_id"):
        return redirect("/login")

    connection = get_connection()
    cursor = connection.cursor()

    if session.get("role") == "staff":
        cursor.execute("""
        SELECT tickets.id, tickets.title, tickets.details, tickets.status, users.name
        FROM tickets
        JOIN users ON users.id = tickets.user_id
        ORDER BY CASE WHEN tickets.status = 'open' THEN 0 ELSE 1 END, tickets.id DESC
        """)

        tickets = [{
            "id": row[0],
            "name": row[4],
            "title": row[1],
            "details": row[2],
            "status": row[3]
        } for row in cursor.fetchall()]
    else:
        cursor.execute("""
        SELECT id, title, details, status 
        FROM tickets 
        WHERE user_id = ?
        ORDER BY CASE WHEN tickets.status = 'open' THEN 0 ELSE 1 END, tickets.id DESC
        """, (session["user_id"],))
        tickets = [{
            "id": row[0],
            "title": row[1],
            "details": row[2],
            "status": row[3]
        } for row in cursor.fetchall()]  # shows only the tickets the user owns.
    connection.close()
    return render_template("tickets.html", tickets=tickets)

@app.route("/tickets/<int:ticket_id>", methods=["GET", "POST"])
def ticket(ticket_id):
    if not session.get("user_id"):
        return redirect("/login")

    connection = get_connection()
    cursor = connection.cursor()

    if session.get("role") == "staff":
        cursor.execute("""
        SELECT tickets.id, tickets.title, tickets.details, tickets.status, users.name
        FROM tickets
        JOIN users ON users.id = tickets.user_id
        WHERE tickets.id = ?
        """, (ticket_id,))
        row = cursor.fetchone()
    else:
        cursor.execute("""
        SELECT tickets.id, tickets.title, tickets.details, tickets.status, users.name
        FROM tickets
        JOIN users ON users.id = tickets.user_id
        WHERE tickets.id = ? AND tickets.user_id = ?
        """, (ticket_id, session["user_id"])) # prevents other users from changing the url and seeing other tickets they do not own.
        row = cursor.fetchone()

    if not row:
        return redirect("/tickets")

    if request.method == "POST":
        if request.form.get("close"):
            cursor.execute("UPDATE tickets SET status = 'closed' WHERE id = ?", (ticket_id,))
            connection.commit()
            connection.close()
            return redirect(f"/tickets/{ticket_id}")
        
        if row[3] == "closed":
            connection.close()
            return redirect(f"/tickets/{ticket_id}")

        message = request.form.get("message")
        if message:
            cursor.execute("INSERT INTO replies(ticket_id, message, user_id, user_role) VALUES(?, ?, ?, ?)", (ticket_id, message, session["user_id"], session["role"])) 
            connection.commit()
        connection.close()
        return redirect(f"/tickets/{ticket_id}")

    cursor.execute("""
    SELECT replies.id, replies.message, replies.user_role, users.name
    FROM replies
    JOIN users ON users.id = replies.user_id
    WHERE replies.ticket_id = ?
    """, (ticket_id,))
    replies = [{
        "id": reply[0],
        "name": reply[3],
        "message": reply[1],
        "user_role": reply[2]
    } for reply in cursor.fetchall()]

    connection.close()
    
    
    ticket = {"id": row[0], "name": row[4], "title": row[1], "details": row[2], "status": row[3]}
    return render_template("ticketall.html", ticket=ticket, replies=replies)

@app.route("/tickets/<int:ticket_id>/delete", methods=["POST"])
def delete_ticket(ticket_id):
    if not session.get("user_id"):
        return redirect("/login")

    if session.get("role") != "staff":
        return redirect("/tickets")
    
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,))
    row = cursor.fetchone()

    if not row or row[0] != "closed":
        connection.close()
        return redirect("/tickets")

    cursor.execute("DELETE FROM replies WHERE ticket_id = ?", (ticket_id,))
    cursor.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
    connection.commit()
    connection.close()
    return redirect("/tickets")

    

if __name__ == "__main__":
    app.run(debug=True)
