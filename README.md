# SkyReserve

This project is a **full-stack web application** simulating a real-world airline ticket reservation system. Customers can search for flights, purchase tickets, track spending, and view dashboards, while airline staff and booking agents manage flights, airplanes, airports, and revenue reporting.  

---


## Technical Stack

- **Backend:** Python, Flask,  MySQL
- **Frontend:** HTML, CSS, JavaScript, Chart.js

---

## Features

### Homepage
![Homepage](docs/images/home.png)
- Guests can search and filter flights by date, origin, destination, and airline.
![Sign-up Page](docs/images/signup.png)
- Users can choose to login or sign-up as a customer, agent, or staff.

### Customer
![Customer Page](docs/images/customer.png)
- Purchase tickets and track personal spending.
- View interactive dashboards showing monthly and yearly spending.

### Booking Agent
- Purchase tickets on behalf of customers for their affiliated airline.
- View upcoming flights purchased for customers.
- Track commission earned from sales.

### Airline Staff
![Staff Page](docs/images/airline_staff.png)
- Add and manage flights, airplanes, and airports.
- Access revenue comparison dashboards (direct vs indirect sales).
- View reports on ticket sales and customer activity.

### Admin
- Role-based access control for all users.
- Monitor system-wide metrics and data integrity.

---

## Getting Started (Local Setup)

### Prerequisites
- Python 3.10+  
- MySQL or XAMPP  
- `pip` package manager  

### Clone the repository
```bash
git clone https://github.com/your-username/airline-ticket-reservation.git
cd airline-ticket-reservation
```

<!-- # SkyReserve

## Quick Start
Test Accounts
- Email: customer@gmail.com, bookingagent@gmail.com
- Password: customer, bookingagent, 

How To Run It On Your Own Computer
1. Download XAMPP and start Apache and MySQL
2. Go to http://localhost/phpmyadmin and import the given database
3. Download repository from github 
4. Run the following commands in the terminal

python3 -m venv venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py (or python3 app.py) -->


Demo logins (all demo1234):
- Customer → customer@demo.com
<!-- - Booking Agent → booking@demo.com -->
<!-- - Staff Admin+Operator (American) → airlinestaff@demo.com -->
- Staff Operator (China Eastern) → operator@demo.com