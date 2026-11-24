# 🍽️ Real-World Restaurant Management System (RMS)

A production-grade, secure, multi-role Django Restaurant Management & Point of Sale (POS) system.

---

## 🌟 Key Features

1. **Super Admin Control Portal**:
   - **Admin Management**: Add Admin, Admin List, Active Admins, Inactive Admins, Suspended Admins.
   - **Restaurant Management**: Business Details, Operational Status (Open/Closed, Operating Hours, Maintenance Mode).
   - **Reports Management**: Review and resolve operational incident reports, kitchen disruptions, and staff escalations submitted by managers.
   - **System Settings**: General Settings (Session timeout, Audit logs), Security Settings (Password complexity, Lockouts), Backup Settings (On-demand & Automated SQLite snapshots).
   - **My Profile**: Dual-card profile info management and secure password change.
   - **Secure Sign Out**: One-click protected logout.

2. **Role-Based Access Control (RBAC)**:
   - **Administrator / Owner**: Full system control, financial analytics, Django admin access.
   - **Restaurant Manager**: Menu & category pricing, inventory restock, staff management, table assignments.
   - **Waiter / Server**: Interactive POS ordering, live floor plan occupancy, ticket tracking.
   - **Chef / Kitchen Staff**: Dedicated real-time Kitchen Display System (KDS), dish prep status toggles.
   - **Cashier**: Billing settlements (Cash, Card, UPI), discount approvals, receipt printing.

2. **Interactive Point of Sale (POS)**:
   - Category filtering (Starters, Pizzas, Pastas, Mains, Desserts, Beverages).
   - Fast instant search for dishes.
   - Cart management with live subtotal, tax calculation, and service charge.
   - Direct Dine-In (with table assignment), Takeaway, and Delivery order routing.

3. **Live Floor Plan & Dining Tables**:
   - Visual floor plan supporting Indoor, Patio, Rooftop, VIP, and Bar areas.
   - Live color-coded statuses: `Available` (Green), `Occupied` (Red), `Reserved` (Yellow), `Needs Cleaning` (Blue).
   - One-click order creation from table.

4. **Kitchen Display System (KDS)**:
   - Real-time kitchen tickets queue.
   - Item-by-item status progression: `Pending` &rarr; `Cooking` &rarr; `Ready`.
   - Special instructions alert for allergies and customer preferences.

5. **Billing & Invoices**:
   - Automated itemized tax and service charge calculations.
   - Payment processing with cash tendered and change return calculator.
   - Support for custom discounts and promotional notes.
   - 80mm thermal printer-ready receipt view (`/billing/<id>/print/`).

6. **Inventory & Stock Management**:
   - Ingredient tracking with units (`kg`, `g`, `L`, `ml`, `pcs`).
   - Low-stock threshold alerts on the dashboard.
   - One-click fast restock form.

---

## 🔒 Security Hardening

- **Environment Isolation**: All credentials, keys, and environment variables stored in `.env` (excluded from git via `.gitignore`).
- **CSRF & XSS Protection**: Cookie security, HttpOnly session flags, automatic HTML escaping.
- **SQL Injection Defense**: 100% parameterized queries via Django ORM.
- **Clickjacking Defense**: `X_FRAME_OPTIONS = 'DENY'`.
- **Granular Decorators**: Custom `@role_required` and `RoleRequiredMixin` on all views.

---

## 🚀 Quick Start Guide

### 1. Activate the Virtual Environment
- **PowerShell**:
  ```powershell
  .\venv\Scripts\Activate.ps1
  ```
- **Command Prompt**:
  ```cmd
  venv\Scripts\activate.bat
  ```

### 2. Database Migrations & Seed Data
The database is already migrated and seeded with sample data. To re-seed anytime:
```bash
python manage.py seed_data
```

### 3. Run the Development Server
```bash
python manage.py runserver
```
Visit: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)

---

## 👥 Default Demo Credentials

| Role | Username | Password | Default Redirect |
|---|---|---|---|
| **Administrator** | `admin` | `admin123` | Executive Dashboard |
| **Manager** | `manager` | `manager123` | Executive Dashboard |
| **Chef / Kitchen** | `chef` | `chef123` | Kitchen Display System (KDS) |
| **Waiter / Server** | `waiter` | `waiter123` | Floor Plan / POS |
| **Cashier** | `cashier` | `cashier123` | Billing & Invoices |

---

## 📁 Complete Folder Structure

```text
Restaurant Management System/
│
├── .env                          # Secret environment variables (KEY, DB, TAX)
├── .env.example                  # Template environment variables
├── .gitignore                    # Git ignore file
├── manage.py                     # Django management entry point
├── requirements.txt              # Pinned production dependencies
├── db.sqlite3                    # SQLite production database
├── README.md                     # Documentation
│
├── restaurant_management/        # Core Project Settings
│   ├── __init__.py
│   ├── asgi.py
│   ├── context_processors.py     # Global restaurant info (currency, name)
│   ├── settings.py               # Security hardened settings
│   ├── urls.py                   # Central URL router
│   └── wsgi.py
│
├── accounts/                     # RBAC & Authentication App
│   ├── admin.py
│   ├── decorators.py             # Role-based access decorators
│   ├── forms.py                  # Login, Staff create/update forms
│   ├── models.py                 # Custom User model with Role choices
│   ├── urls.py
│   └── views.py
│
├── menu/                         # Menu & Inventory App
│   ├── admin.py
│   ├── forms.py                  # MenuItem, Category, Ingredient forms
│   ├── models.py                 # Category, MenuItem, DietaryTag, Ingredient
│   ├── urls.py
│   └── views.py
│
├── tables/                       # Dining Tables & Reservations App
│   ├── admin.py
│   ├── forms.py                  # Table & Reservation forms
│   ├── models.py                 # DiningTable, Reservation models
│   ├── urls.py
│   └── views.py
│
├── orders/                       # POS & Kitchen Display System (KDS) App
│   ├── admin.py
│   ├── forms.py                  # Order & OrderItem forms
│   ├── models.py                 # Order, OrderItem models
│   ├── urls.py
│   └── views.py                  # POS interface, KDS live board
│
├── billing/                      # Invoicing & Payments App
│   ├── admin.py
│   ├── forms.py                  # Payment & Discount forms
│   ├── models.py                 # Invoice, Payment models
│   ├── urls.py
│   └── views.py                  # Thermal receipt printing & checkout
│
├── dashboard/                    # Executive Dashboard App
│   ├── management/
│   │   └── commands/
│   │       └── seed_data.py      # Automated data population command
│   ├── urls.py
│   └── views.py                  # Live metrics & overview
│
├── static/                       # Static assets
│   ├── css/
│   ├── js/
│   └── images/
│
├── media/                        # User uploaded images (food items, receipts)
│
├── templates/                    # Presentation Layer (Bootstrap 5)
│   ├── base.html                 # Main master layout & responsive sidebar
│   ├── 403.html                  # Forbidden error page
│   ├── 404.html                  # Not found error page
│   ├── 500.html                  # Server error page
│   ├── accounts/
│   │   ├── login.html
│   │   ├── profile.html
│   │   ├── staff_list.html
│   │   └── staff_form.html
│   ├── dashboard/
│   │   └── index.html
│   ├── menu/
│   │   ├── menu_list.html
│   │   ├── item_form.html
│   │   ├── category_list.html
│   │   ├── inventory_list.html
│   │   └── ingredient_form.html
│   ├── tables/
│   │   ├── floor_plan.html
│   │   ├── table_form.html
│   │   ├── reservation_list.html
│   │   └── reservation_form.html
│   ├── orders/
│   │   ├── pos.html              # Point Of Sale terminal UI
│   │   ├── order_list.html
│   │   ├── order_detail.html
│   │   └── kitchen_display.html  # Live KDS board
│   └── billing/
│       ├── invoice_detail.html
│       ├── invoice_list.html
│       └── print_receipt.html    # Thermal receipt
│
└── venv/                         # Python Virtual Environment
```
