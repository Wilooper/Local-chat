import os
import uuid
import datetime
from flask import Flask, render_template, redirect, url_for, request, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from flask_socketio import SocketIO, join_room, emit
from werkzeug.security import generate_password_hash, check_password_hash

# Create extension instances (not bound to an app yet)
db = SQLAlchemy()
login_manager = LoginManager()
socketio = SocketIO(manage_session=True)

def create_app():
    app = Flask(__name__)
    
    # Basic configuration
    app.config['SECRET_KEY'] = 'secret!'
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chat.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # Save uploads in static/uploads for serving as static files
    app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    
    db.init_app(app)
    login_manager.init_app(app)
    socketio.init_app(app)
    
    # ---------------------------
    # Database Models
    # ---------------------------
    contacts = db.Table('contacts',
        db.Column('user_id', db.Integer, db.ForeignKey('user.id')),
        db.Column('contact_id', db.Integer, db.ForeignKey('user.id'))
    )

    class User(UserMixin, db.Model):
        id = db.Column(db.Integer, primary_key=True)
        uid = db.Column(db.String(10), unique=True, nullable=False)
        username = db.Column(db.String(80), unique=True, nullable=False)
        email = db.Column(db.String(120), unique=True, nullable=False)
        password_hash = db.Column(db.String(128), nullable=False)
        # Many-to-many relationship for contacts/friends
        chat_list = db.relationship('User', secondary=contacts,
                                    primaryjoin=(contacts.c.user_id == id),
                                    secondaryjoin=(contacts.c.contact_id == id),
                                    backref='contacts')

    class Message(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        sender_id = db.Column(db.Integer, db.ForeignKey('user.id'))
        # Relationship so we can easily get sender.username
        sender = db.relationship('User', foreign_keys=[sender_id])
        receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # for private messages
        group_id = db.Column(db.Integer, db.ForeignKey('chat_group.id'), nullable=True)  # for group messages
        content = db.Column(db.Text, nullable=True)
        timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
        filename = db.Column(db.String(200), nullable=True)  # for uploaded files/images

    class ChatGroup(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(100), nullable=False)
        admin_id = db.Column(db.Integer, db.ForeignKey('user.id'))
        members = db.relationship('User', secondary='group_members', backref='groups')

    group_members = db.Table('group_members',
        db.Column('group_id', db.Integer, db.ForeignKey('chat_group.id')),
        db.Column('user_id', db.Integer, db.ForeignKey('user.id'))
    )

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # ---------------------------
    # Routes for Authentication & Dashboard
    # ---------------------------
    @app.route('/')
    def index():
        return redirect(url_for('login'))

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            username = request.form['username']
            email = request.form['email']
            password = request.form['password']
            if User.query.filter_by(username=username).first():
                flash('Username already exists.')
                return redirect(url_for('register'))
            uid = str(uuid.uuid4())[:8]
            new_user = User(username=username, email=email, uid=uid,
                            password_hash=generate_password_hash(password))
            db.session.add(new_user)
            db.session.commit()
            flash('Registered successfully. Please login.')
            return redirect(url_for('login'))
        return render_template('register.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            username = request.form['username']
            password = request.form['password']
            user = User.query.filter_by(username=username).first()
            if user and check_password_hash(user.password_hash, password):
                login_user(user)
                return redirect(url_for('dashboard'))
            else:
                flash('Invalid username or password.')
        return render_template('login.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        return redirect(url_for('login'))

    @app.route('/dashboard')
    @login_required
    def dashboard():
        return render_template('dashboard.html', uid=current_user.uid)

    @app.route('/profile')
    @login_required
    def profile():
        return render_template('profile.html', user=current_user)

    @app.route('/change_password', methods=['GET', 'POST'])
    @login_required
    def change_password():
        if request.method == 'POST':
            current_password = request.form['current_password']
            new_password = request.form['new_password']
            confirm_password = request.form['confirm_password']
            if not check_password_hash(current_user.password_hash, current_password):
                flash('Current password is incorrect.')
            elif new_password != confirm_password:
                flash('New passwords do not match.')
            else:
                current_user.password_hash = generate_password_hash(new_password)
                db.session.commit()
                flash('Password updated successfully.')
                return redirect(url_for('profile'))
        return render_template('change_password.html')

    @app.route('/private_chat', methods=['GET', 'POST'])
    @login_required
    def private_chat():
        if request.method == 'POST':
            uid_search = request.form['uid']
            user = User.query.filter_by(uid=uid_search).first()
            if user and user != current_user:
                if user not in current_user.chat_list:
                    current_user.chat_list.append(user)
                    db.session.commit()
                flash('User added to your chat list.')
            else:
                flash('User not found.')
        return render_template('private_chat.html', contacts=current_user.chat_list)

    @app.route('/group_chat')
    @login_required
    def group_chat():
        groups = current_user.groups
        return render_template('group_chat.html', groups=groups)

    @app.route('/create_group', methods=['GET', 'POST'])
    @login_required
    def create_group():
        if request.method == 'POST':
            group_name = request.form['group_name']
            group = ChatGroup(name=group_name, admin_id=current_user.id)
            group.members.append(current_user)
            db.session.add(group)
            db.session.commit()
            flash('Group created successfully.')
            return redirect(url_for('group_chat'))
        return render_template('create_group.html')

    @app.route('/add_to_group/<int:group_id>', methods=['GET', 'POST'])
    @login_required
    def add_to_group(group_id):
        group = ChatGroup.query.get_or_404(group_id)
        if group.admin_id != current_user.id:
            flash('Only the group admin can add users.')
            return redirect(url_for('group_chat'))
        if request.method == 'POST':
            uid_search = request.form['uid']
            user = User.query.filter_by(uid=uid_search).first()
            if user and (user in current_user.chat_list) and (user not in group.members):
                group.members.append(user)
                db.session.commit()
                flash('User added to group.')
            else:
                flash('User not found or not in your chat list.')
        return render_template('add_to_group.html', group=group)

    @app.route('/chat/<chat_type>/<int:chat_id>')
    @login_required
    def chat(chat_type, chat_id):
        if chat_type == 'private':
            other_user = User.query.get_or_404(chat_id)
            messages = Message.query.filter(
                ((Message.sender_id == current_user.id) & (Message.receiver_id == other_user.id)) |
                ((Message.sender_id == other_user.id) & (Message.receiver_id == current_user.id))
            ).order_by(Message.timestamp).all()
            return render_template('chat.html', messages=messages, chat_type='private', chat_id=other_user.id)
        elif chat_type == 'group':
            group = ChatGroup.query.get_or_404(chat_id)
            if current_user not in group.members:
                flash('You are not a member of this group.')
                return redirect(url_for('dashboard'))
            messages = Message.query.filter_by(group_id=group.id).order_by(Message.timestamp).all()
            return render_template('chat.html', messages=messages, chat_type='group', chat_id=group.id)
        else:
            flash('Invalid chat type.')
            return redirect(url_for('dashboard'))

    @app.route('/clear_history/<chat_type>/<int:chat_id>', methods=['POST'])
    @login_required
    def clear_history(chat_type, chat_id):
        if chat_type == 'private':
            other_user = User.query.get_or_404(chat_id)
            Message.query.filter(
                ((Message.sender_id == current_user.id) & (Message.receiver_id == other_user.id)) |
                ((Message.sender_id == other_user.id) & (Message.receiver_id == current_user.id))
            ).delete(synchronize_session=False)
        elif chat_type == 'group':
            Message.query.filter_by(group_id=chat_id).delete(synchronize_session=False)
        db.session.commit()
        flash('Chat history cleared.')
        return redirect(url_for('chat', chat_type=chat_type, chat_id=chat_id))

    @app.route('/upload', methods=['POST'])
    @login_required
    def upload():
        file = request.files.get('file')
        if file:
            filename = str(uuid.uuid4()) + "_" + file.filename
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            return filename
        return ''
    
    @socketio.on('join')
    def handle_join(data):
        room = data['room']
        join_room(room)
        emit('status', {'msg': current_user.username + ' has entered the room.'}, room=room)
    
    @socketio.on('text')
    def handle_text(data):
        room = data['room']
        msg = data['msg']
        chat_type = data['chat_type']
        chat_id = data['chat_id']
        filename = data.get('filename')
        message = Message(sender_id=current_user.id, content=msg, filename=filename)
        if chat_type == 'private':
            message.receiver_id = chat_id
        elif chat_type == 'group':
            message.group_id = chat_id
        db.session.add(message)
        db.session.commit()
        emit('message', {
             'username': current_user.username,
             'timestamp': message.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
             'content': msg,
             'filename': filename,
             'message_id': message.id
        }, room=room)
    
    @socketio.on('delete_message')
    def handle_delete_message(data):
        message_id = data.get('message_id')
        room = data.get('room')
        msg_obj = Message.query.get(message_id)
        if msg_obj and str(msg_obj.sender_id) == str(current_user.get_id()):
            db.session.delete(msg_obj)
            db.session.commit()
            emit('message_deleted', {'message_id': message_id}, room=room)
        else:
            emit('error', {'msg': 'You are not authorized to delete this message.'}, room=room)
    
    return app

if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        db.create_all()
    socketio.run(app, debug=True)
