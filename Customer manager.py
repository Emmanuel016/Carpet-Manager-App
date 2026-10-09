import pdb
import os 
import re
import json
import secrets
import bcrypt
import logging
from functools import wraps
from datetime import datetime, date, timedelta, timezone
import requests
import base64
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask import (Flask, request, render_template, jsonify, redirect, url_for, send_file, abort, session)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
key = os.environ.get("FLASK_SECRET_KEY")
if key:
  app.secret_key = key
else:
  try:
    app.secret_key = secrets.token_bytes(32)
    logger.info('Using generated secret key')
  except Exception as e:
    logger.warning(f'Generating a new secret key', e)
    app.secret_key = secrets.token_urlsafe(32)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "database")
os.makedirs(DB_DIR, exist_ok=True)

_session_cookie_secure = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() in ("1", "true", "yes")

app.config.update(
    SESSION_COOKIE_SECURE=_session_cookie_secure,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
    SESSION_COOKIE_NAME= f'__Secure-{_session_cookie_secure}-customer_manager_session' if _session_cookie_secure else 'customer_manager_session'
)

def get_path(filename):
  return os.path.join(BASE_DIR, filename)

#Database creation
DATABASE_URL = os.environ.get('DATABASE_URL', "").strip()

if DATABASE_URL:
  if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

  if DATABASE_URL.startswith("postgresql://"):
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
  else:
    app.config['SQLALCHEMY_DATABASE_URI'] = f"postgresql://{DATABASE_URL}"
else:
  logger.warning("DATABASE_URL not set. Falling back to SQLite for development.")
  db_path = os.path.join(DB_DIR, "customers.db")
  app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{db_path}"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

class Serializer:
  def to_dict(self):
    result = {}
    for column in self.__table__.columns:
      value = getattr(self, column.name)
      # Format datetime objects as ISO strings for JSON compatibility
      if isinstance(value, datetime):
        value = value.isoformat() if value else None
      result[column.name] = value
    return result

class Customer(db.Model, Serializer):
    __tablename__ = "customers"
    id = db.Column(db.Integer, primary_key=True)
    first = db.Column(db.String(150), nullable=False)
    second = db.Column(db.String(150), nullable=False)
    town = db.Column(db.String(150), nullable=False)
    no = db.Column(db.String(150), nullable=False)
    date_added = db.Column(db.Date)

class User(db.Model, Serializer):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), nullable=False)
    password = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), nullable=False)
    company = db.Column(db.String(150), nullable=False)
    date_added = db.Column(db.Date)


class Quotes(db.Model, Serializer):
    __tablename__ = "quotes"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    quote_number = db.Column(db.String(150), nullable=False)
    quote = db.Column(db.Text)
    date_added = db.Column(db.Date)
    cost = db.Column(db.Float)
    customer_fullname = db.Column(db.String(300))
    room_size = db.Column(db.String(100))
    underlay = db.Column(db.String(50))
    overall_cost = db.Column(db.Float)
    labour_cost = db.Column(db.Float)
    customer = db.relationship("Customer", backref=db.backref("quotes", lazy=True))

def initialize_database():
    """Create database tables safely if they do not exist"""
    try:
        logger.info("Initializing database...")
        with app.app_context():
            db.create_all()
            logger.info("Database tables created/verified successfully")
    except Exception as e:
        logger.error(f"Database initialization error: {str(e)}")
        raise

def check_database():
    try:
        with app.app_context():
            db.session.query(Customer).first()
            logger.info("Database connection successful")
    except Exception as e:
        logger.error(f"Database connection error: {str(e)}")
        raise

class Customer_Manager:
  def __init__(self):
    self.db = db
    self.app = app
    self.Customers = []
    self.Quotes = []
    self.Total_Costs = 0.0
    self.current_user = None
    self.carpet_cost = 22.50
    self.gripper_cost = 1.10
    self.cost_per_hour = 65
    self.c_for_first_step = 5.99
    self.c_for_monarch = 7.99
    self.c_for_royal = 60.00

  
  #Utilities
  def sanitize_input(self, input_string:str):
    if not input_string:
      return ""
    # Strip basic HTML tags
    clean = re.sub(r'<[^>]*>', '', input_string)
    return clean.strip()

  def validate_password(self, password:str):
    if not password:
      return False, "Password is required"
    if len(password) < 5:
      return False, "Password must be at least 6 characters"
    if len(password) > 128:
      return False, "Password must not exceed 128 characters"
    has_digit = any(c.isdigit() for c in password)
    if not has_digit:
      return False, "Password must contain numbers"
    return True, ""
  
  # DATABASE UTILITES
  def get_all_customers(self):
    with self.app.app_context():
      return self.db.session.query(Customer).all()

  def get_customer_by_id(self, customer_id):
    with self.app.app_context():
     return Customer.query.get(customer_id)
  
  def get_customer_by_fullname(self, customer_fullname):
    with self.app.app_context():
     return Customer.query.filter_by(customer_fullname=customer_fullname).first()

  def get_all_quotes(self):
    with self.app.app_context():
      return self.db.session.query(Quote).all()

  def get_quote_by_id(self, quote_id):
    with self.app.app_context():
      return Quotes.query.get(quote_id)

  def get_quote_by_name(self, customer_name):
    with self.app.app_context():
      customer = self.get_customer_by_fullname(customer_name)
      if not customer:
        return False, "Customer not found"
      quote = self.db.session.query(Quotes).filter_by(customer_id=customer.id).first()
      if not quote:
        return False, "Quote not found"
      return True, quote

  def get_quotes_by_customer_id(self, customer_id):
    with self.app.app_context():
      return self.db.session.query(Quotes).filter_by(customer_id=customer_id).all()

  def add_quote_details(self, quote):
    with self.app.app_context():
      for i, value in quote.items():
        if not value:
          return False, f"{i} is required"
      
      date_added = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
      details = Quotes(
        customer_id=quote['customer_id'],
        quote_number=f"{quote['customer_id']}-{len((quote['customer_id'])) + 1}",
        room_size=quote['room_size'],
        quote=quote['quote_description'],
        customer_fullname=quote['customer_fullname'],
        underlay_type=quote['underlay_type'],
        width=quote['width'],
        length=quote['length'],
        labour=quote['labour'],
        total_cost=quote['total'],
        status=quote['status'],
        date_added=date_added,      
        overall_cost=quote['overall_cost']
      )

      self.db.session.add(details)
      logger.info(f"Quote added: {details.id}")
      self.db.session.commit()
      return True, "Quote added successfully"

  def add_quote_to_existing_customer(self, quote):
    with self.app.app_context():
      return self.add_quote_details(quote)
    
  def search_customers(self, query):
    with self.app.app_context():
      return self.db.session.query(Customer).filter(Customer.name.contains(query)).all()

  def delete_quote(self, quote_id):
    with self.app.app_context():
      quote = self.db.session.query(Quotes).filter_by(id=quote_id).first()
      if not quote:
        return False, "Quote not found"
      self.db.session.delete(quote)
      self.db.session.commit()
      return True, "Quote deleted successfully"

  def tick_quote(self, quote_id):
    with self.app.app_context():
      quote = self.db.session.query(Quotes).filter_by(id=quote_id).first()
      if not quote:
        return False, "Quote not found"
      quote.status = "completed"
      self.db.session.commit()
      return True, "Quote completed successfully"

  def add_customer_details(self, details):
    with self.app.app_context():
      customer = Customer(
        first=details.get('first'),
        second=details.get('second'),
        town=details.get('town'),
        no=details.get('no'),
        date_added=datetime.now().date()
      )
      self.db.session.add(customer)
      self.db.session.commit()
      return True, "Customer added successfully"

  def compute_total_expense(self, length, width, underlay):
     area = length * width
     perimeter = 2 * (length + width)
     if underlay == "f":
         c = self.c_for_first_step
         underlay_name = "First step"
     elif underlay == "m":
         c = self.c_for_monarch
         underlay_name = "Monarch"
     else:
         c = self.c_for_royal
         underlay_name = "Royal"
     cost = area * c
     cost2 = area * self.carpet_cost
     cost3 = perimeter * self.gripper_cost
     labour = (area / 16) * self.cost_per_hour
     total = cost + cost2 + cost3 + labour
     return underlay_name, labour, total


  # AUTHENTICATION
  def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
      if 'user_id' not in session:
        return redirect(url_for('login'))
      return f(*args, **kwargs)
    return decorated_function

  def login(self):
    try:
      if request.method == "GET":
        return render_template('login.html')

      elif request.method == "POST":
        username = request.form.get('username').strip()
        password = request.form.get('password').strip()

        if not username or not password:
          return render_template('login.html', error='Username and password are required')

        user = db.session.query(User).filter_by(username=username).first()
        if not user or not bcrypt.checkpw(password.encode('utf-8'), user.password.encode('utf-8')):
          return render_template('login.html', error='Username or password is incorrect.')

        session.clear()
        session['user_id'] = user.id
        session['username'] = user.username
        session.permanent = True
        return redirect(url_for('dashboard'))

    except Exception as e:
      logger.error(f"Login error: {str(e)}")
      return render_template('login.html', error='An error occurred during login')

  def register(self):
    try:
      if request.method == "GET":
        return render_template('register.html')

      elif request.method == "POST":
        username = request.form.get('username').strip()
        password = request.form.get('password').strip()
        email = request.form.get('email').strip()
        company = request.form.get('company').strip()

        if not username or not password or not email:
          return render_template('register.html', error='Username, password, and email are required')

        existing = db.session.query(User).filter_by(username=username).first()
        if existing:
          return render_template('register.html', error='Username already exists')

        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        user = User(username=username, password=hashed_password, email=email, company=company or "Personal")
        db.session.add(user)
        db.session.commit()

        session.clear()
        session['user_id'] = user.id
        session['username'] = username
        session.permanent = True

        logger.info(f"User {username} registered successfully.")
        return redirect(url_for('dashboard'))
    except Exception as e:
      logger.error(f"Register error: {str(e)}")
      return render_template('register.html', error=str(e))
  
  @login_required
  def api_customer_details(self):
    if request.method == "POST":
      if not self.get_customer():
        return render_template('details.html', error='No Customer Found')
      
      data = request.json or {}
      customer_name = data.get('customer_id').strip()
      if not customer_name:
        logger.error("Customer name is required")
        return jsonify({"error": "Customer name is required"}), 400
   
      customer_id = self.get_customer_by_name(customer_name)
      if not customer_id:
        logger.error("Customer not found")
        return jsonify({"error": "Customer not found"}), 404
      
      try:
        customer_id = int(customer_id.id)
      except Exception as e:
        logger.error(f"Invalid customer ID: {str(e)}")
        return jsonify({"error": "Invalid customer ID"}), 400

      logger.info(f"Fetching details for customer ID: {customer_id}")
      customer_data = self.get_customer_data(customer_id)
      return jsonify({
        'status': 'success',
        'customer': {
          'id': customer_data.id,
          'name': customer_data.name,
          'email': customer_data.email,
          'phone': customer_data.phone,
          'address': customer_data.address,
          'created_at': customer_data.created_at.isoformat() if customer_data.created_at else None
        }
      })
    
  @login_required
  def api_customers(self):
    logger.info("Getting all customers")
    if not self.current_user:
      logger.error("User not logged in")
      return jsonify({'status': 'error', 'message': 'User not logged in'})
    
    if not self.get_all_customers():
      logger.error("No customers found")
      return jsonify({'status': 'error', 'message': 'No customers found'})
    
    customers = self.get_all_customers()
    customers_list = [customer.to_dict() for customer in customers]
    logger.info(f"Customers retrieved: {len(customers_list)}")
    return jsonify({'status': 'success', 'customers': customers_list, 'count': len(customers_list)})


  @login_required
  def api_customers_search(self):
    if not self.get_all_customers():
      logger.error("No customers found")
      return jsonify({'status': 'error', 'message': 'No customers found'}), 404

    query = request.args.get('q', '').strip()
    if not query:
      logger.error("Search query is required")
      return jsonify({'status': 'error', 'message': 'Search query is required'}), 400
    
    logger.info(f"Searching for customers with query: {query}")
    customers = self.search_customers(query)
    if not customers:
      logger.error("No customers found")
      return jsonify({'status': 'error', 'message': 'No customers found'}), 404
    return jsonify({
      'status': 'success',
      'customers': [
        {
          'id': customer.id,
          'name': customer.name,
          'email': customer.email,
          'phone': customer.phone,
          'address': customer.address,
          'created_at': customer.created_at.isoformat() if customer.created_at else None
        }
        for customer in customers
      ]
    })
    
  @login_required
  def api_add_customer(self):
    data = request.json or {}
    if not data:
      logger.error("No data provided")
      return jsonify({'status': 'error', 'message': 'No data provided'})

    first = data.get('first')
    second = data.get('second')
    town = data.get('town')
    no = data.get('no')

    if not first or not second or not town or not no:
      logger.error("All fields are required")
      return jsonify({'status': 'error', 'message': 'All fields are required'})

    with app.app_context():
      customer = Customer(
        first=first,
        second=second,
        town=town,
        no=no,
        date_added=datetime.now().date()
      )
      db.session.add(customer)
      db.session.commit()
      logger.info(f"Customer added: {first} {second}")
      return jsonify({'status': 'success', 'message': 'Customer added successfully'})

  @login_required
  def api_add_quote(self):
    data = request.json or {}
    if not data:
      logger.error("No data provided")
      return jsonify({'status': 'error', 'message': 'No data provided'})

    customer_name = data.get('customer_id')
    quote_description = data.get('quote_data')
    underlay = data.get('underlay')
    width = data.get('width')
    length = data.get('length')

    if not customer_name or not quote_description or not underlay or not width or not length:
      logger.error("All fields are required")
      return jsonify({'status': 'error', 'message': 'All fields are required'})

    with app.app_context():
      customer = db.session.query(Customer).filter(
        db.func.concat(Customer.first, ' ', Customer.second) == customer_name
      ).first()

      if not customer:
        return jsonify({'status': 'error', 'message': 'Customer not found'})

      total_expense = self.compute_total_expense(float(length), float(width), underlay)
      underlay_type, labour, total = total_expense
      overall_cost = total / 1.2

      quote = {'customer_id': customer.id,'customer_fullname': customer_name, 'room_size': f"{width}x{length}",  'length': length, 'width': width,
      'quote_description': quote_description, 'underlay': underlay_type, 'labour': labour, 'total': overall_cost, 'overall_cost': total, 'status': 'pending'}

      added, message = self.add_quote_details(quote)
      if not added:
        return jsonify({'status': 'error', 'message': message})
      return jsonify({'status': 'success', 'message': message})

  @login_required
  def api_add_quote_to_existing(self):
    if request.method == 'GET':
      logger.info("Getting existing customers")
      customers = self.get_all_customers()
      message = "Retrieved existing customers"
      customers_list = [customer.to_dict() for customer in customers]
      logger.info(f"Customers retrieved: {len(customers_list)}")
      return jsonify({'status': 'success', 'message': message, 'customers': customers_list})
   
    elif request.method == 'POST':
      data = request.json or {}
      if not data:
        logger.error("No data provided")
        return jsonify({'status': 'error', 'message': 'No data provided'})
      
      customer_name = data.get('customer_name')
      customer_name = self.sanitize_input(customer_name)
      quote_description = data.get('quote_description')
      quote_description = self.sanitize_input(quote_description)
      underlay = data.get('underlay')
      underlay = self.sanitize_input(underlay)
      width, length = data.get('width'), data.get('length')
      width, length = self.sanitize_input(width), self.sanitize_input(length)
      
      if not customer_name or not quote_description or not underlay or not width or not length:
        logger.error("All fields are required")
        return jsonify({'status': 'error', 'message': 'All fields are required'})

      if customer_name not in [customer.fullname for customer in self.get_all_customers()]:
        logger.error("Customer not found")
        return jsonify({'status': 'error', 'message': 'Customer not found'})

      try:
        customer = self.get_customer_by_fullname(customer_name)
        customer_id = customer.id if customer else None
      except Exception as e:
        logger.error(f"Error getting customer: {e}")
        logger.error(f"Error occurred while getting customer with name: {customer_name}")
        return jsonify({'status': 'error', 'message': 'Error getting customer'})

      total_expense = self.compute_total_expense(length, width, underlay)
      underlay_type, labour, total = total_expense
      overall_cost = total / 1.2

      details = {'customer_id': customer_id,'customer_fullname': customer_name, 'room_size': f'{length}x{width}', 'length': length, 'width': width,
      'quote_description': quote_description, 'underlay_type': underlay_type, 'labour': labour, 'total': overall_cost, 'overall_cost': total, 'status': 'pending'}
      quote, message = self.add_quote_to_existing_customer(details)
      
      if not quote:
        logger.error(f"Failed to add quote: {message}") 
        return jsonify({'status': 'error', 'message': message})
      logger.info(f"Quote added: {quote.id}")
      return jsonify({'status': 'success', 'message': message})
  
  @login_required
  def api_tick_quote(self):
    data = request.json or {}
    if not data:
      logger.error("No data provided")
      return jsonify({'status': 'error', 'message': 'No data provided'})
    
    try:
      quote_name = data.get('quote_name')
      quote_name = self.sanitize_input(quote_name)
    
      if not quote_name:
        logger.error("Quote name is required")
        return jsonify({'status': 'error', 'message': 'Quote name is required'})
    
      quote_id = self.get_quote_by_name(quote_name)
      logger.info(f"Quote ID: {quote_id}")
      if not quote_id:
        logger.error(f"Quote not found for name: {quote_name}")
        return jsonify({'status': 'error', 'message': f'Quote not found for name: {quote_name}'})
    
      quote = self.tick_quote(quote_id.id)
    
      if not quote:
        logger.error(f"Failed to tick quote for ID: {quote_id.id}")
        return jsonify({'status': 'error', 'message': f'Failed to tick quote for ID: {quote_id.id}'})
      logger.info(f"Quote ticked: {quote_name}")
      return jsonify({'status': 'success', 'message': 'Quote ticked successfully'})
   
    except Exception as e:
      logger.error(f"Error ticking quote: {str(e)}")
      return jsonify({'status': 'error', 'message': f'Error ticking quote: {str(e)}'})

  @login_required
  def api_get_all_quotes(self):
    logger.info("Getting all quotes")
    try:
      if not self.current_user:
        logger.error("User not logged in")
        return jsonify({'status': 'error', 'message': 'User not logged in'})
    
      if not self.get_all_quotes():
        logger.error("No quotes found")
        return jsonify({'status': 'error', 'message': 'No quotes found'})
    
      quotes = self.get_all_quotes()
      quotes_list = [quote.to_dict() for quote in quotes]
      logger.info(f"Quotes retrieved: {len(quotes_list)}")
      return jsonify({'status': 'success', 'quotes': quotes_list, 'count': len(quotes_list)})
   
    except Exception as e:
      logger.error(f"Error getting all quotes: {str(e)}")
      return jsonify({'status': 'error', 'message': f'Error getting all quotes: {str(e)}'})

  @login_required
  def api_delete_quote(self, quote_id):
   try:
    if not self.current_user:
      logger.error("User not logged in")
      return jsonify({'status': 'error', 'message': 'User not logged in'})

    if not self.get_all_quotes():
      logger.error("No quotes found")
      return jsonify({'status': 'error', 'message': 'No quotes found'})
    
    data = request.json
    quote_name = data.get('quote_name')
    quote_name = self.sanitize_input(quote_name)
    
    quote_id = self.get_quote_by_name(quote_name)
    if not quote_id:
      logger.error(f"Quote not found for name: {quote_name}")
      return jsonify({'status': 'error', 'message': f'Quote not found for name: {quote_name}'})
    
    quote = self.get_quote_by_name(quote_name)
    quote_id = quote.id
    logger.info(f"Quote: {quote}")
    if not quote:
      logger.error(f"Quote not found for ID: {quote_id}")
      return jsonify({'status': 'error', 'message': f'Quote not found for ID: {quote_id}'})
    
    if not self.delete_quote(quote_id):
      logger.error(f"Failed to delete quote for ID: {quote_id}")
      return jsonify({'status': 'error', 'message': f'Failed to delete quote for ID: {quote_id}'})
    
    logger.info(f"Quote deleted: {quote_id.id}")
    return jsonify({'status': 'success', 'message': 'Quote deleted successfully'})
   
   except Exception as e:
    logger.error(f"Error deleting quote: {str(e)}")
    db.session.rollback()
    return jsonify({'status': 'error', 'message': f'Error deleting quote: {str(e)}'})
   
  @app.route("/api/get_quote_by_name/<string:quote_name>", methods=['GET'])
  def get_quote_by_name(self, quote_name):
   try:
    if not self.current_user:
      logger.error("User not logged in")
      return jsonify({'status': 'error', 'message': 'User not logged in'})

    if not self.get_all_quotes():
      logger.error("No quotes found")
      return jsonify({'status': 'error', 'message': 'No quotes found'})
    
    quote = self.get_quote_by_name(quote_name)
    if not quote:
      logger.error(f"Quote not found for name: {quote_name}")
      return jsonify({'status': 'error', 'message': f'Quote not found for name: {quote_name}'})
    
    logger.info(f"Quote retrieved: {quote}")
    return jsonify({'status': 'success', 'quote': quote.to_dict()})

   except Exception as e:
    logger.error(f"Error getting quote by name: {str(e)}")
    return jsonify({'status': 'error', 'message': f'Error getting quote by name: {str(e)}'})

  @app.route("/api/delete_customer/<string:customer_name>", methods=['DELETE'])
  @login_required
  def delete_customer(self, customer_name):
   try:
    if not self.current_user:
      logger.error("User not logged in")
      return jsonify({'status': 'error', 'message': 'User not logged in'})
    
    if not self.get_all_customers():
      logger.error("No customers found")
      return jsonify({'status': 'error', 'message': 'No customers found'})
    
    customer = self.get_customer_by_name(customer_name)
    if not customer:
      logger.error(f"Customer not found for name: {customer_name}")
      return jsonify({'status': 'error', 'message': f'Customer not found for name: {customer_name}'})
    
    if not self.delete_customer(customer.id):
      logger.error(f"Failed to delete customer for name: {customer_name}")
      return jsonify({'status': 'error', 'message': f'Failed to delete customer for name: {customer_name}'})
    
    logger.info(f"Customer deleted: {customer_name}, ID: {customer.id}")
   
    return jsonify({'status': 'success', 'message': f'Customer deleted successfully, ID: {customer.id}, Name: {customer.name}'})
   except Exception as e:
    logger.error(f"Error deleting customer: {str(e)}")
    db.session.rollback()
    return jsonify({'status': 'error', 'message': f'Error deleting customer: {str(e)}'})

  # UTILITY PAGE ROUTES
  def logout(self):
    self.current_user = None
    session.clear()
    logger.info("User logged out")
    return redirect(url_for("login"))

  @login_required
  def dashboard(self):
    logger.info("Dashboard accessed")
    user_id = session.get("user_id")
    user = db.session.get(User, user_id)

    if not user:
        logger.warning(f"Dashboard access attempted with invalid user_id: {user_id}")
        session.clear()
        return redirect(url_for("login"))

    if not user_id:
      logger.error("User not logged in")
      return redirect(url_for("login"))

    else:
      logger.info("User logged in, showing dashboard")
      with app.app_context():
        customers = db.session.query(Customer).all()
        customer_count = len(customers) if customers else 0

        quotes = db.session.query(Quotes).all()
        quote_count = len(quotes) if quotes else 0
        pending_quotes = len([q for q in quotes if q.status == 'pending']) if quotes else 0

    return render_template('dashboard.html',customer_count=customer_count,quote_count=quote_count,pending_quotes=pending_quotes)

  def user_register(self):
    logger.info("User register page accessed")
    return render_template('register.html')

  def user_login(self):
      logger.info("Login page accessed")
      return redirect(url_for("dashboard"))

  @login_required
  def view_details(self):
    logger.info("Details page accessed")
    with app.app_context():
      customers = db.session.query(Customer).all()
      if customers and len(customers) > 0:
        customer = customers[0]
        return render_template('details.html', customer=customer)
      else:
        return render_template('details.html', error='No customers found yet. Add your first customer from the dashboard.')

  @login_required
  def settings(self):
    logger.info("Settings page accessed")
    return render_template('settings.html')

# Initialize the manager and bind routes
cm = Customer_Manager()

# Bind the routes to the instance
app.route("/login", methods=["GET", "POST"])(cm.login)
app.route("/register", methods=["GET", "POST"])(cm.register)
app.route("/api/customer_by_id/details", methods=["POST"])(cm.api_customer_details)
app.route("/api/customers", methods=['GET'])(cm.api_customers)
app.route("/api/customers/search", methods=['GET'])(cm.api_customers_search)
app.route("/api/add_customer", methods=['POST'])(cm.api_add_customer)
app.route("/api/add_quote", methods=['POST'])(cm.api_add_quote)
app.route("/api/add_quote/to_existing", methods=['POST', 'GET'])(cm.api_add_quote_to_existing)
app.route("/api/tick_quote", methods=['POST'])(cm.api_tick_quote)
app.route("/api/get_all_quotes", methods=['GET'])(cm.api_get_all_quotes)
app.route("/api/delete_quote/<int:quote_id>", methods=['DELETE'])(cm.api_delete_quote)
app.route("/logout")(cm.logout)
app.route("/dashboard")(cm.dashboard)
app.route('/user_register')(cm.user_register)
app.route("/")(cm.user_login) #https://github.com/Emmanuel016/Carpet-Manager-App.git
app.route("/details")(cm.view_details)
app.route("/settings")(cm.settings)

initialize_database()
if __name__ == "__main__":
    print("Starting Customer Manager...")
    port = int(os.environ.get("PORT", 5000))
    debug = os.getenv("DEBUG", "False").lower() == "true"
    auto_reload = os.environ.get("FLASK_AUTO_RELOAD", "True").lower() == "true"
    use_reloader = auto_reload
    app.run(host="0.0.0.0", debug=debug, port=port, use_reloader=use_reloader)