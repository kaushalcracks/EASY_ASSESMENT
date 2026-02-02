from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import psycopg2
import os
import io
import re
import pandas as pd
from hashlib import md5
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash
from flask_session import Session
from pdf2image import convert_from_bytes
import google.generativeai as genai
from PIL import Image

# Load environment variables
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY", "your_secret_key")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise EnvironmentError("GEMINI_API_KEY environment variable is not set")

genai.configure(api_key=GEMINI_API_KEY)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = './flask_sessions'
Session(app)

UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
REPORT_FILE = "student_scores.csv"

# Ensure the CSV file is properly initialized
if not os.path.exists(REPORT_FILE):
    df = pd.DataFrame(columns=["Name", "Class & Section", "Roll No", "Score", "Feedback"])
    df.to_csv(REPORT_FILE, index=False)

# Initialize Flask extensions
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = "register_login"

# Database connection
def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return None

# User Model
class User(UserMixin):
    def __init__(self, id, name, email):
        self.id = id
        self.name = name
        self.email = email

@login_manager.user_loader
def load_user(user_id):
    # Robust check for None or "None" string to prevent database errors
    if user_id is None or user_id == 'None':
        return None
        
    conn = get_db_connection()
    if conn:
        try:
            user_id_int = int(user_id)
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, email FROM users WHERE id = %s", (user_id_int,))
            user = cursor.fetchone()
            conn.close()
            if user:
                return User(id=user[0], name=user[1], email=user[2])
        except (ValueError, TypeError):
            conn.close()
            return None
    return None

# Initialize database
def init_db():
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(100) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL
            )
        ''')
        conn.commit()
        cursor.close()
        conn.close()

@app.route("/", methods=["GET"])
def home():
    # MODIFIED: Redirect directly to the dashboard
    return redirect(url_for("dashboard"))

@app.route("/register", methods=["GET", "POST"])
def register_login():
    if request.method == "POST":
        action = request.form["action"]

        if action == "register":
            name = request.form["name"]
            email = request.form["email"]
            password = request.form["password"]
            hashed_password = generate_password_hash(password, method="pbkdf2:sha256")

            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                try:
                    cursor.execute("INSERT INTO users (name, email, password) VALUES (%s, %s, %s)", 
                                   (name, email, hashed_password))
                    conn.commit()
                    flash("✅ Registration successful! You can now log in.", "success")
                except Exception as e:
                    print(e)
                    conn.rollback()
                finally:
                    cursor.close()
                    conn.close()

        elif action == "login":
            email = request.form["email"]
            password = request.form["password"]

            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, name, email, password FROM users WHERE email = %s", (email,))
                user = cursor.fetchone()
                cursor.close()
                conn.close()

                if user and check_password_hash(user[3], password):
                    user_obj = User(id=user[0], name=user[1], email=user[2])
                    login_user(user_obj)
                    flash("✅ Login successful!", "success")
                    return redirect(url_for("dashboard"))
                else:
                    flash("❌ Invalid email or password!", "danger")

    return render_template("register.html")

@app.route("/dashboard", methods=["GET", "POST"])
# MODIFIED: Removed @login_required decorator
def dashboard():
    if request.method == "POST":
        name = request.form["name"]
        class_section = request.form["class_section"]
        roll_no = request.form["roll_no"]
        user_score = request.form["user_score"]
        pdf_file = request.files["pdf_file"]

        if not (name and class_section and roll_no and user_score and pdf_file):
            flash("All fields are required!")
            return redirect(url_for("dashboard"))

        filename = pdf_file.filename
        save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        pdf_file.save(save_path)

        try:
            images = convert_pdf_to_images(save_path)
            
            # MODIFIED: Capture full feedback
            full_feedback_list = []
            for image in images:
                feedback = evaluate_image(image, user_score)
                full_feedback_list.append(feedback)

            final_feedback_text = "\n\n---\n\n".join(full_feedback_list)
            
            # MODIFIED: Extract score for flash message, save full feedback
            score_match = re.search(r"SCORE:\s*(\d+\.?\d*\s*/\s*\d+\.?\d*)", final_feedback_text, re.IGNORECASE)
            display_score = score_match.group(1).strip() if score_match else "Not found"
            
            save_to_file(name, class_section, roll_no, display_score, final_feedback_text)
            
            flash(f"Evaluation Complete! Final Score: {display_score}")
        except Exception as e:
            flash(f"Error: {str(e)}")

        return redirect(url_for("dashboard"))

    # MODIFIED: Hardcode a name since no user is logged in
    return render_template("index.html", name="Guest")

@app.route("/report")
@login_required
def report():
    return send_file(REPORT_FILE, as_attachment=True)

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("✅ Logged out successfully!", "success")
    return redirect(url_for("register_login"))

# PDF and AI Processing Functions
def convert_pdf_to_images(pdf_path):
    with open(pdf_path, "rb") as f:
        return convert_from_bytes(f.read())

def generate_image_hash(image):
    return md5(convert_image_to_bytes(image)).hexdigest()

def convert_image_to_bytes(image):
    with io.BytesIO() as buffer:
        image.save(buffer, format="PNG")
        return buffer.getvalue()

def evaluate_image(image, user_score):
    # MODIFIED: Use stable model and detailed prompt, return full text
    model = genai.GenerativeModel(model_name="gemini-2.5-flash")
    
    prompt = f"""
    You are an AI teaching assistant evaluating a handwritten answer. The maximum score is {user_score}.
    Please provide your evaluation in the following structure:
    1.  **Score**: Start with the score in the format 'SCORE: X/{user_score}'.
    2.  **Feedback**: Provide a brief analysis of the student's mistakes and suggest areas for improvement in just 10 words
    """

    try:
        response = model.generate_content([prompt, image])
        if response and hasattr(response, 'text'):
            return response.text # Return the full feedback
        return "Evaluation not available."
    except Exception as e:
        print(f"Error evaluating image: {e}")
        return "Error evaluating image"

def save_to_file(name, class_section, roll_no, score, feedback):
    # MODIFIED: Add "Feedback" column and save feedback data
    report_columns = ["Name", "Class & Section", "Roll No", "Score", "Feedback"]
    
    if not os.path.exists(REPORT_FILE):
        df = pd.DataFrame(columns=report_columns)
        df.to_csv(REPORT_FILE, index=False)
    
    df = pd.read_csv(REPORT_FILE)
    new_data = pd.DataFrame([{
        "Name": name, 
        "Class & Section": class_section, 
        "Roll No": roll_no, 
        "Score": score,
        "Feedback": feedback
    }])
    df = pd.concat([df, new_data], ignore_index=True)
    df.to_csv(REPORT_FILE, index=False)

if __name__ == "__main__":
    # You might need to run init_db() once if you start with a fresh database
    # init_db() 
    app.run(debug=True)