

from cs50 import SQL
from flask import Flask, flash, redirect, render_template, request, session
from flask_session import Session
from werkzeug.security import check_password_hash, generate_password_hash

db = SQL("sqlite:///ticket.db")


# Configure application
app = Flask(__name__)

@app.route("/")
def singup():
    return render_template("signin.html")
@app.route("/signin", methods =["GET","POST"])
def login():
    if request.method == "POST":
        if not request.form.get("username"):
            return ("you must enter a username")
        elif not request.form.get("email_address"):
            return ("you must enter an email address",400)
        db.execute("INSERT INTO users (username, email_address) VALUES(?, ?)",
                   request.form.get("username"), request.form.get("email_address"),)

    return render_template("index.html")
# send the seller to the registration or login page
@app.route("/register")
def register():
    return render_template("register.html")
@app.route("/account")
def account():
    return render_template("account.html")
#
@app.route("/seller", methods=["GET", "POST"])
def seller():
    if request.method == "POST":
        if not request.form.get("username"):
            return ("you did not enter a username")
        elif not request.form.get("password"):
            return("you must enter a password")
        elif request.form.get("password") != request.form.get("confirm_password"):
            return("your passwords don't match")
        db.execute("INSERT INTO registrants (username, password) VALUES(?, ?)",
                   request.form.get("username"),request.form.get("password"),)
    return render_template("seller.html")









