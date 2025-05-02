from flask import Flask, render_template, request, session, redirect, url_for, flash, jsonify
from flask_socketio import SocketIO, emit
import sqlite3
import os
from werkzeug.utils import secure_filename
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
app.config['UPLOAD_FOLDER'] = 'static/avatars'
# Ensure upload folder exists
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])
socketio = SocketIO(app)

# Database connection
def get_db_connection():
    conn = sqlite3.connect('nana.db')
    conn.row_factory = sqlite3.Row
    return conn

# Initialize database
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            image TEXT,
            likes INTEGER DEFAULT 0,
            dislikes INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS friends (
            user_id INTEGER,
            friend_id INTEGER,
            status TEXT NOT NULL,
            PRIMARY KEY (user_id, friend_id),
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (friend_id) REFERENCES users (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER,
            user2_id INTEGER,
            FOREIGN KEY (user1_id) REFERENCES users (id),
            FOREIGN KEY (user2_id) REFERENCES users (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            sender_id INTEGER,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (chat_id) REFERENCES chats (id),
            FOREIGN KEY (sender_id) REFERENCES users (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reactions (
            user_id INTEGER,
            post_id INTEGER,
            reaction TEXT NOT NULL,
            PRIMARY KEY (user_id, post_id),
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (post_id) REFERENCES posts (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            user_id INTEGER,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (post_id) REFERENCES posts (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
    conn.close()

# Send notification to user
def send_notification(user_id, content):
    conn = get_db_connection()
    cursor = conn.cursor()
    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('INSERT INTO notifications (user_id, content, created_at) VALUES (?, ?, ?)',
                   (user_id, content, created_at))
    conn.commit()
    cursor.execute('SELECT content, created_at FROM notifications WHERE user_id = ?', (user_id,))
    notifications = cursor.fetchall()
    conn.close()
    socketio.emit('update_notifications', {
        'user_id': user_id,
        'notifications': [(n['content'], n['created_at']) for n in notifications]
    }, room=str(user_id))

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('home'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ? AND password = ?', (username, password))
        user = cursor.fetchone()
        conn.close()
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('home'))
        flash('Invalid credentials', 'error')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
            conn.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username already exists', 'error')
        finally:
            conn.close()
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/home')
def home():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    
    # Fetch posts with user reactions and latest comment
    cursor.execute('''
        SELECT p.id, p.content, p.created_at, u.username, p.image, p.likes, p.dislikes,
               r.reaction,
               (SELECT c.content, c.created_at, u2.username
                FROM comments c
                JOIN users u2 ON c.user_id = u2.id
                WHERE c.post_id = p.id
                ORDER BY c.created_at DESC LIMIT 1) as latest_comment
        FROM posts p
        JOIN users u ON p.user_id = u.id
        LEFT JOIN reactions r ON p.id = r.post_id AND r.user_id = ?
        ORDER BY p.created_at DESC
    ''', (user_id,))
    posts = cursor.fetchall()
    
    # Fetch friends
    cursor.execute('''
        SELECT u.username, u.id
        FROM friends f
        JOIN users u ON u.id = f.friend_id
        WHERE f.user_id = ? AND f.status = 'accepted'
    ''', (user_id,))
    friends = cursor.fetchall()
    
    # Fetch friend requests
    cursor.execute('''
        SELECT u.id, u.username
        FROM friends f
        JOIN users u ON u.id = f.user_id
        WHERE f.friend_id = ? AND f.status = 'pending'
    ''', (user_id,))
    friend_requests = cursor.fetchall()
    
    # Fetch chats with unread message count
    cursor.execute('''
        SELECT c.id, u.username, 
               (SELECT COUNT(*) FROM messages m 
                WHERE m.chat_id = c.id 
                AND m.sender_id != ? 
                AND m.created_at > 
                    (SELECT MAX(created_at) FROM messages m2 WHERE m2.chat_id = c.id AND m2.sender_id = ?)) as unread_count
        FROM chats c
        JOIN users u ON (u.id = c.user1_id OR u.id = c.user2_id) AND u.id != ?
        WHERE c.user1_id = ? OR c.user2_id = ?
    ''', (user_id, user_id, user_id, user_id, user_id))
    chats = cursor.fetchall()
    
    # Fetch notifications
    cursor.execute('SELECT content, created_at FROM notifications WHERE user_id = ?', (user_id,))
    notifications = cursor.fetchall()
    conn.close()
    
    if user:
        return render_template('home.html', username=user['username'], posts=posts, friends=friends,
                               friend_requests=friend_requests, notifications=notifications, chats=chats)
    return redirect(url_for('login'))

@app.route('/profile/<int:user_id>')
def profile(user_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    if user:
        return render_template('profile.html', username=user['username'], user_id=user_id)
    return redirect(url_for('home'))

@app.route('/profile', methods=['GET', 'POST'])
def edit_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        cursor.execute('UPDATE users SET username = ?, password = ? WHERE id = ?', (username, password, user_id))
        conn.commit()
        session['username'] = username
        flash('Profile updated successfully', 'success')
        return redirect(url_for('home'))
    cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return render_template('edit_profile.html', username=user['username'])

@app.route('/create_post', methods=['POST'])
def create_post():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    content = request.form['content']
    image = request.files.get('image')
    image_filename = None
    if image and image.filename:
        image_filename = secure_filename(image.filename)
        image.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))
    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO posts (user_id, content, created_at, image) VALUES (?, ?, ?, ?)',
                   (user_id, content, created_at, image_filename))
    post_id = cursor.lastrowid
    cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
    username = cursor.fetchone()['username']
    conn.commit()
    conn.close()
    socketio.emit('new_post', {
        'id': post_id,
        'username': username,
        'content': content,
        'created_at': created_at,
        'image': image_filename,
        'likes': 0,
        'dislikes': 0
    })
    return redirect(url_for('home'))

@app.route('/like_post/<int:post_id>', methods=['POST'])
def like_post(post_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT reaction FROM reactions WHERE user_id = ? AND post_id = ?', (user_id, post_id))
    current_reaction = cursor.fetchone()
    if current_reaction and current_reaction['reaction'] == 'like':
        cursor.execute('DELETE FROM reactions WHERE user_id = ? AND post_id = ?', (user_id, post_id))
        cursor.execute('UPDATE posts SET likes = likes - 1 WHERE id = ?', (post_id,))
    else:
        if current_reaction and current_reaction['reaction'] == 'dislike':
            cursor.execute('UPDATE posts SET dislikes = dislikes - 1 WHERE id = ?', (post_id,))
        cursor.execute('INSERT OR REPLACE INTO reactions (user_id, post_id, reaction) VALUES (?, ?, ?)',
                       (user_id, post_id, 'like'))
        cursor.execute('UPDATE posts SET likes = likes + 1 WHERE id = ?', (post_id,))
    conn.commit()
    cursor.execute('SELECT likes, dislikes FROM posts WHERE id = ?', (post_id,))
    post = cursor.fetchone()
    conn.close()
    socketio.emit('post_reaction_updated', {
        'post_id': post_id,
        'likes': post['likes'],
        'dislikes': post['dislikes'],
        'user_id': user_id,
        'user_reaction': 'like' if not current_reaction or current_reaction['reaction'] != 'like' else None
    })
    return redirect(url_for('home'))

@app.route('/dislike_post/<int:post_id>', methods=['POST'])
def dislike_post(post_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT reaction FROM reactions WHERE user_id = ? AND post_id = ?', (user_id, post_id))
    current_reaction = cursor.fetchone()
    if current_reaction and current_reaction['reaction'] == 'dislike':
        cursor.execute('DELETE FROM reactions WHERE user_id = ? AND post_id = ?', (user_id, post_id))
        cursor.execute('UPDATE posts SET dislikes = dislikes - 1 WHERE id = ?', (post_id,))
    else:
        if current_reaction and current_reaction['reaction'] == 'like':
            cursor.execute('UPDATE posts SET likes = likes - 1 WHERE id = ?', (post_id,))
        cursor.execute('INSERT OR REPLACE INTO reactions (user_id, post_id, reaction) VALUES (?, ?, ?)',
                       (user_id, post_id, 'dislike'))
        cursor.execute('UPDATE posts SET dislikes = dislikes + 1 WHERE id = ?', (post_id,))
    conn.commit()
    cursor.execute('SELECT likes, dislikes FROM posts WHERE id = ?', (post_id,))
    post = cursor.fetchone()
    conn.close()
    socketio.emit('post_reaction_updated', {
        'post_id': post_id,
        'likes': post['likes'],
        'dislikes': post['dislikes'],
        'user_id': user_id,
        'user_reaction': 'dislike' if not current_reaction or current_reaction['reaction'] != 'dislike' else None
    })
    return redirect(url_for('home'))

@app.route('/search_user', methods=['POST'])
def search_user():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    username = request.form.get('username')
    if not username:
        flash('Please enter a username to search.', 'error')
        return redirect(url_for('home'))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, username FROM users WHERE username LIKE ?', ('%' + username + '%',))
    users = cursor.fetchall()
    conn.close()
    return render_template('search_results.html', users=users, search_query=username)

@app.route('/send_friend_request/<int:friend_id>', methods=['POST'])
def send_friend_request(friend_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    if user_id == friend_id:
        flash('You cannot send a friend request to yourself.', 'error')
        return redirect(url_for('home'))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM friends WHERE user_id = ? AND friend_id = ?', (user_id, friend_id))
    if cursor.fetchone():
        flash('Friend request already sent or you are already friends.', 'error')
    else:
        cursor.execute('INSERT INTO friends (user_id, friend_id, status) VALUES (?, ?, ?)',
                       (user_id, friend_id, 'pending'))
        conn.commit()
        cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
        sender_username = cursor.fetchone()['username']
        socketio.emit('new_friend_request', {
            'sender_id': user_id,
            'sender_username': sender_username,
            'friend_id': friend_id
        }, room=str(friend_id))
        send_notification(friend_id, f"{sender_username} sent you a friend request.")
        flash('Friend request sent.', 'success')
    conn.close()
    return redirect(url_for('home'))

@app.route('/accept_friend/<int:friend_id>', methods=['POST'])
def accept_friend(friend_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE friends SET status = ? WHERE user_id = ? AND friend_id = ?',
                   ('accepted', friend_id, user_id))
    cursor.execute('INSERT OR IGNORE INTO friends (user_id, friend_id, status) VALUES (?, ?, ?)',
                   (user_id, friend_id, 'accepted'))
    # Create or get chat
    cursor.execute('SELECT id FROM chats WHERE (user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)',
                   (user_id, friend_id, friend_id, user_id))
    chat = cursor.fetchone()
    if not chat:
        cursor.execute('INSERT INTO chats (user1_id, user2_id) VALUES (?, ?)', (user_id, friend_id))
        chat_id = cursor.lastrowid
    else:
        chat_id = chat['id']
    conn.commit()
    cursor.execute('SELECT username FROM users WHERE id = ?', (friend_id,))
    friend_username = cursor.fetchone()['username']
    cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
    user_username = cursor.fetchone()['username']
    conn.close()
    socketio.emit('friend_request_accepted', {
        'friend_id': friend_id,
        'friend_username': friend_username,
        'chat_id': chat_id,
        'user_id': user_id
    }, room=str(user_id))
    socketio.emit('friend_request_accepted', {
        'friend_id': user_id,
        'friend_username': user_username,
        'chat_id': chat_id,
        'user_id': friend_id
    }, room=str(friend_id))
    send_notification(friend_id, f"{user_username} accepted your friend request.")
    send_notification(user_id, f"You are now friends with {friend_username}.")
    return redirect(url_for('home'))

@app.route('/reject_friend/<int:friend_id>', methods=['POST'])
def reject_friend(friend_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM friends WHERE user_id = ? AND friend_id = ?', (friend_id, user_id))
    conn.commit()
    conn.close()
    socketio.emit('friend_request_rejected', {'friend_id': friend_id, 'user_id': user_id}, room=str(friend_id))
    return redirect(url_for('home'))

@app.route('/create_chat/<int:friend_id>')
def create_chat(friend_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM chats WHERE (user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)',
                   (user_id, friend_id, friend_id, user_id))
    chat = cursor.fetchone()
    if not chat:
        cursor.execute('INSERT INTO chats (user1_id, user2_id) VALUES (?, ?)', (user_id, friend_id))
        chat_id = cursor.lastrowid
        conn.commit()
    else:
        chat_id = chat['id']
    conn.close()
    return redirect(url_for('chat', chat_id=chat_id))

@app.route('/chat/<int:chat_id>')
def chat(chat_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user1_id, user2_id FROM chats WHERE id = ?', (chat_id,))
    chat = cursor.fetchone()
    if not chat or (user_id not in [chat['user1_id'], chat['user2_id']]):
        flash('Invalid chat.', 'error')
        return redirect(url_for('home'))
    friend_id = chat['user2_id'] if chat['user1_id'] == user_id else chat['user1_id']
    cursor.execute('SELECT username FROM users WHERE id = ?', (friend_id,))
    friend = cursor.fetchone()
    cursor.execute('SELECT m.content, m.created_at, u.username FROM messages m JOIN users u ON m.sender_id = u.id WHERE m.chat_id = ?', (chat_id,))
    messages = cursor.fetchall()
    conn.close()
    return render_template('chat.html', chat_id=chat_id, friend=friend['username'], messages=messages)

@app.route('/send_message/<int:chat_id>', methods=['POST'])
def send_message(chat_id):
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'})
    user_id = session['user_id']
    content = request.json.get('content')
    if not content:
        return jsonify({'status': 'error', 'message': 'Message cannot be empty'})
    conn = get_db_connection()
    cursor = conn.cursor()
    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('INSERT INTO messages (chat_id, sender_id, content, created_at) VALUES (?, ?, ?, ?)',
                   (chat_id, user_id, content, created_at))
    conn.commit()
    cursor.execute('SELECT user1_id, user2_id FROM chats WHERE id = ?', (chat_id,))
    chat = cursor.fetchone()
    receiver_id = chat['user2_id'] if chat['user1_id'] == user_id else chat['user1_id']
    cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
    sender_username = cursor.fetchone()['username']
    conn.close()
    socketio.emit('new_message', {
        'chat_id': chat_id,
        'sender_id': user_id,
        'sender_username': sender_username,
        'content': content,
        'created_at': created_at
    }, room=str(receiver_id))
    return jsonify({'status': 'success', 'content': content, 'created_at': created_at, 'username': sender_username})

@app.route('/add_comment', methods=['POST'])
def add_comment():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'})
    user_id = session['user_id']
    post_id = request.json.get('post_id')
    content = request.json.get('content')
    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO comments (post_id, user_id, content, created_at) VALUES (?, ?, ?, ?)',
                   (post_id, user_id, content, created_at))
    conn.commit()
    cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
    username = cursor.fetchone()['username']
    conn.close()
    return jsonify({'status': 'success', 'username': username, 'created_at': created_at})

@app.route('/get_comments/<int:post_id>')
def get_comments(post_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT c.content, c.created_at, u.username
        FROM comments c
        JOIN users u ON c.user_id = u.id
        WHERE c.post_id = ?
        ORDER BY c.created_at ASC
    ''', (post_id,))
    comments = [{'content': row['content'], 'created_at': row['created_at'], 'username': row['username']}
                for row in cursor.fetchall()]
    conn.close()
    return jsonify({'comments': comments})

@app.route('/clear_notifications', methods=['POST'])
def clear_notifications():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'})
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM notifications WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()
    socketio.emit('update_notifications', {'user_id': user_id, 'notifications': []}, room=str(user_id))
    return jsonify({'status': 'success'})

@socketio.on('join_room')
def on_join_room(user_id):
    socketio.join_room(str(user_id))

if __name__ == '__main__':
    init_db()
    socketio.run(app, debug=True)
