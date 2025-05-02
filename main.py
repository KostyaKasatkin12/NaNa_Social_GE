from flask import Flask, render_template, redirect, url_for, request, session, flash, jsonify
from flask_socketio import SocketIO, emit, join_room
import sqlite3
import os
from werkzeug.utils import secure_filename
import jinja2

app = Flask(__name__)
app.secret_key = 'your_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*")

UPLOAD_FOLDER = 'static/avatars'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def init_db():
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        password TEXT NOT NULL,
        description TEXT,
        relationship_status TEXT DEFAULT 'не интересуюсь',
        avatar TEXT,
        city TEXT
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        image TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS friends (
        user_id INTEGER NOT NULL,
        friend_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, friend_id),
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (friend_id) REFERENCES users(id)
    )''')
    cursor.execute("PRAGMA table_info(friends)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'created_at' not in columns:
        cursor.execute("ALTER TABLE friends ADD COLUMN created_at TIMESTAMP")
        cursor.execute("UPDATE friends SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
    cursor.execute('''CREATE TABLE IF NOT EXISTS chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user1_id INTEGER NOT NULL,
        user2_id INTEGER NOT NULL,
        FOREIGN KEY (user1_id) REFERENCES users(id),
        FOREIGN KEY (user2_id) REFERENCES users(id)
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_read INTEGER DEFAULT 0,
        FOREIGN KEY (chat_id) REFERENCES chats(id),
        FOREIGN KEY (sender_id) REFERENCES users(id)
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS post_reactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        reaction TEXT NOT NULL,
        UNIQUE(post_id, user_id),
        FOREIGN KEY (post_id) REFERENCES posts(id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS post_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (post_id) REFERENCES posts(id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    cursor.execute("PRAGMA table_info(chat_messages)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'is_read' not in columns:
        cursor.execute("ALTER TABLE chat_messages ADD COLUMN is_read INTEGER DEFAULT 0")
        cursor.execute("UPDATE chat_messages SET is_read = 0 WHERE is_read IS NULL")
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'city' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN city TEXT")
    conn.commit()
    conn.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@socketio.on('connect')
def handle_connect():
    print('Клиент подключился')

@socketio.on('join_room')
def on_join(room):
    join_room(str(room))
    print(f'Клиент присоединился к комнате: {room}')

def send_notifications(user_id):
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT users.username, 
               (SELECT COUNT(*) FROM chat_messages 
                WHERE chat_messages.chat_id = chats.id 
                AND chat_messages.sender_id != ? 
                AND chat_messages.is_read = 0) AS unread_count
        FROM chats 
        JOIN users ON (chats.user1_id = users.id OR chats.user2_id = users.id)
        WHERE (chats.user1_id = ? OR chats.user2_id = ?) 
        AND users.id != ?
        AND (SELECT COUNT(*) FROM chat_messages 
             WHERE chat_messages.chat_id = chats.id 
             AND chat_messages.sender_id != ? 
             AND chat_messages.is_read = 0) > 0
    """, (user_id, user_id, user_id, user_id, user_id))
    unread_notifications = cursor.fetchall()
    notifications = [(f"{username} sent you {unread_count} message(s)", None) for username, unread_count in unread_notifications]
    cursor.execute("SELECT content, created_at FROM notifications WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    notifications.extend(cursor.fetchall())
    total_unread = len(notifications)
    conn.close()
    print(f"Отправка уведомлений для user_id {user_id}: {notifications}, total_unread: {total_unread}")
    socketio.emit('update_notifications', {
        'user_id': user_id,
        'notifications': notifications,
        'total_unread': total_unread
    }, room=str(user_id))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = sqlite3.connect('nana.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ? AND password = ?", (username, password))
        user = cursor.fetchone()
        if user:
            session['user_id'] = user[0]
            conn.close()
            return redirect(url_for('home'))
        else:
            conn.close()
            flash('Invalid username or password', 'error')
            return redirect(url_for('register'))
    return render_template('login.html')

@app.route('/')
def home():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    cursor.execute("""
        SELECT posts.id, posts.content, posts.created_at, users.username, posts.image,
               (SELECT COUNT(*) FROM post_reactions WHERE post_id = posts.id AND reaction = 'like') AS likes,
               (SELECT COUNT(*) FROM post_reactions WHERE post_id = posts.id AND reaction = 'dislike') AS dislikes,
               (SELECT reaction FROM post_reactions WHERE post_id = posts.id AND user_id = ?) AS user_reaction,
               (SELECT json_group_array(json_array(c.content, c.created_at, u2.username))
                FROM post_comments c
                JOIN users u2 ON c.user_id = u2.id
                WHERE c.post_id = posts.id
                ORDER BY c.created_at DESC
                LIMIT 1) AS latest_comment
        FROM posts 
        JOIN users ON posts.user_id = users.id 
        ORDER BY posts.created_at DESC
    """, (user_id,))
    posts_raw = cursor.fetchall()
    import json
    posts = []
    for post in posts_raw:
        latest_comment = json.loads(post[8])[0] if post[8] and json.loads(post[8]) else None
        posts.append(post[:8] + (latest_comment,))
    cursor.execute("""
        SELECT users.username, users.id FROM friends 
        JOIN users ON friends.friend_id = users.id
        WHERE friends.user_id = ? AND friends.status = 'accepted'
    """, (user_id,))
    friends = cursor.fetchall()
    cursor.execute("""
        SELECT users.id, users.username FROM friends 
        JOIN users ON friends.user_id = users.id
        WHERE friends.friend_id = ? AND friends.status = 'pending'
    """, (user_id,))
    friend_requests = cursor.fetchall()
    cursor.execute("""
        SELECT chats.id, users.username,
               (SELECT COUNT(*) FROM chat_messages 
                WHERE chat_messages.chat_id = chats.id 
                AND chat_messages.sender_id != ? 
                AND chat_messages.is_read = 0) AS unread_count
        FROM chats 
        JOIN users ON (chats.user1_id = users.id OR chats.user2_id = users.id)
        WHERE (chats.user1_id = ? OR chats.user2_id = ?) AND users.id != ?
    """, (user_id, user_id, user_id, user_id))
    chats = cursor.fetchall()
    cursor.execute("""
        SELECT users.username, 
               (SELECT COUNT(*) FROM chat_messages 
                WHERE chat_messages.chat_id = chats.id 
                AND chat_messages.sender_id != ? 
                AND chat_messages.is_read = 0) AS unread_count
        FROM chats 
        JOIN users ON (chats.user1_id = users.id OR chats.user2_id = users.id)
        WHERE (chats.user1_id = ? OR chats.user2_id = ?) 
        AND users.id != ?
        AND (SELECT COUNT(*) FROM chat_messages 
             WHERE chat_messages.chat_id = chats.id 
             AND chat_messages.sender_id != ? 
             AND chat_messages.is_read = 0) > 0
    """, (user_id, user_id, user_id, user_id, user_id))
    unread_notifications = cursor.fetchall()
    notifications = [(f"{username} sent you {unread_count} message(s)", None) for username, unread_count in unread_notifications]
    cursor.execute("SELECT content, created_at FROM notifications WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    notifications.extend(cursor.fetchall())
    conn.close()
    if user:
        send_notifications(user_id)
        return render_template('home.html', username=user[0], posts=posts, friends=friends,
                             friend_requests=friend_requests, notifications=notifications, chats=chats)
    return redirect(url_for('login'))

@app.route('/like_post/<int:post_id>', methods=['POST'])
def like_post(post_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    cursor.execute("SELECT reaction FROM post_reactions WHERE post_id = ? AND user_id = ?", (post_id, user_id))
    existing_reaction = cursor.fetchone()

    if existing_reaction:
        if existing_reaction[0] == 'like':
            cursor.execute("DELETE FROM post_reactions WHERE post_id = ? AND user_id = ?", (post_id, user_id))
        else:
            cursor.execute("UPDATE post_reactions SET reaction = 'like' WHERE post_id = ? AND user_id = ?",
                          (post_id, user_id))
    else:
        cursor.execute("INSERT INTO post_reactions (post_id, user_id, reaction) VALUES (?, ?, 'like')",
                      (post_id, user_id))
        cursor.execute("SELECT user_id, username FROM posts JOIN users ON posts.user_id = users.id WHERE posts.id = ?", (post_id,))
        post_owner = cursor.fetchone()
        if post_owner and post_owner[0] != user_id:
            cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,))
            liker_username = cursor.fetchone()[0]
            cursor.execute("INSERT INTO notifications (user_id, content) VALUES (?, ?)",
                          (post_owner[0], f"{liker_username} liked your post"))
            conn.commit()
            send_notifications(post_owner[0])

    conn.commit()
    cursor.execute("""
        SELECT (SELECT COUNT(*) FROM post_reactions WHERE post_id = ? AND reaction = 'like') AS likes,
               (SELECT COUNT(*) FROM post_reactions WHERE post_id = ? AND reaction = 'dislike') AS dislikes,
               (SELECT reaction FROM post_reactions WHERE post_id = ? AND user_id = ?) AS user_reaction
    """, (post_id, post_id, post_id, user_id))
    reaction_data = cursor.fetchone()
    likes, dislikes, user_reaction = reaction_data if reaction_data else (0, 0, None)
    conn.close()
    socketio.emit('post_reaction_updated', {
        'post_id': post_id,
        'likes': likes,
        'dislikes': dislikes,
        'user_id': user_id,
        'user_reaction': user_reaction
    })
    return redirect(request.referrer or url_for('home'))

@app.route('/dislike_post/<int:post_id>', methods=['POST'])
def dislike_post(post_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    cursor.execute("SELECT reaction FROM post_reactions WHERE post_id = ? AND user_id = ?", (post_id, user_id))
    existing_reaction = cursor.fetchone()

    if existing_reaction:
        if existing_reaction[0] == 'dislike':
            cursor.execute("DELETE FROM post_reactions WHERE post_id = ? AND user_id = ?", (post_id, user_id))
        else:
            cursor.execute("UPDATE post_reactions SET reaction = 'dislike' WHERE post_id = ? AND user_id = ?",
                          (post_id, user_id))
    else:
        cursor.execute("INSERT INTO post_reactions (post_id, user_id, reaction) VALUES (?, ?, 'dislike')",
                      (post_id, user_id))
        cursor.execute("SELECT user_id, username FROM posts JOIN users ON posts.user_id = users.id WHERE posts.id = ?", (post_id,))
        post_owner = cursor.fetchone()
        if post_owner and post_owner[0] != user_id:
            cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,))
            disliker_username = cursor.fetchone()[0]
            cursor.execute("INSERT INTO notifications (user_id, content) VALUES (?, ?)",
                          (post_owner[0], f"{disliker_username} disliked your post"))
            conn.commit()
            send_notifications(post_owner[0])

    conn.commit()
    cursor.execute("""
        SELECT (SELECT COUNT(*) FROM post_reactions WHERE post_id = ? AND reaction = 'like') AS likes,
               (SELECT COUNT(*) FROM post_reactions WHERE post_id = ? AND reaction = 'dislike') AS dislikes,
               (SELECT reaction FROM post_reactions WHERE post_id = ? AND user_id = ?) AS user_reaction
    """, (post_id, post_id, post_id, user_id))
    reaction_data = cursor.fetchone()
    likes, dislikes, user_reaction = reaction_data if reaction_data else (0, 0, None)
    conn.close()
    socketio.emit('post_reaction_updated', {
        'post_id': post_id,
        'likes': likes,
        'dislikes': dislikes,
        'user_id': user_id,
        'user_reaction': user_reaction
    })
    return redirect(request.referrer or url_for('home'))

@app.route('/get_comments/<int:post_id>', methods=['GET'])
def get_comments(post_id):
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT post_comments.content, post_comments.created_at, users.username
        FROM post_comments
        JOIN users ON post_comments.user_id = users.id
        WHERE post_comments.post_id = ?
        ORDER BY post_comments.created_at ASC
    """, (post_id,))
    comments = cursor.fetchall()
    conn.close()
    return jsonify({
        'status': 'success',
        'comments': [{'content': comment[0], 'created_at': comment[1], 'username': comment[2]} for comment in comments]
    })

@app.route('/add_comment', methods=['POST'])
def add_comment():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'}), 401
    user_id = session['user_id']
    data = request.get_json()
    post_id = data['post_id']
    content = data['content']
    
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO post_comments (post_id, user_id, content) VALUES (?, ?, ?)",
                  (post_id, user_id, content))
    conn.commit()
    cursor.execute("SELECT created_at FROM post_comments WHERE id = LAST_INSERT_ROWID()")
    created_at = cursor.fetchone()[0]
    cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,))
    username = cursor.fetchone()[0]
    
    # Уведомление владельца поста
    cursor.execute("SELECT user_id, content FROM posts WHERE id = ?", (post_id,))
    post_data = cursor.fetchone()
    post_owner_id, post_content = post_data
    if post_owner_id != user_id:
        # Ограничим длину содержимого поста до 20 символов
        short_content = post_content[:20] + ('...' if len(post_content) > 20 else '')
        cursor.execute("INSERT INTO notifications (user_id, content) VALUES (?, ?)",
                      (post_owner_id, f"{username} commented on your post: \"{short_content}\""))
        conn.commit()
        send_notifications(post_owner_id)
    
    conn.close()
    return jsonify({
        'status': 'success',
        'username': username,
        'created_at': created_at
    })

@app.route('/clear_notifications', methods=['POST'])
def clear_notifications():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'}), 401
    user_id = session['user_id']
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notifications WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    send_notifications(user_id)
    return jsonify({'status': 'success', 'message': 'Notifications cleared'}), 200

@app.route('/create_post', methods=['POST'])
def create_post():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    content = request.form['content']
    image = request.files.get('image')
    image_filename = None
    if image and allowed_file(image.filename):
        filename = secure_filename(image.filename)
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image.save(image_path)
        image_filename = filename
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO posts (user_id, content, image) VALUES (?, ?, ?)",
                  (user_id, content, image_filename))
    conn.commit()
    cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,))
    username = cursor.fetchone()[0]
    cursor.execute("SELECT id, created_at FROM posts WHERE id = LAST_INSERT_ROWID()")
    post_id, created_at = cursor.fetchone()
    conn.close()
    socketio.emit('new_post', {
        'id': post_id,
        'username': username,
        'content': content,
        'image': image_filename,
        'created_at': created_at,
        'likes': 0,
        'dislikes': 0,
        'user_reaction': None
    })
    return redirect(url_for('home'))

# Список городов России
RUSSIAN_CITIES = [
    'Москва', 'Санкт-Петербург', 'Новосибирск', 'Екатеринбург', 'Казань',
    'Нижний Новгород', 'Челябинск', 'Самара', 'Омск', 'Ростов-на-Дону',
    'Уфа', 'Красноярск', 'Воронеж', 'Пермь', 'Волгоград'
]

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    if request.method == 'POST':
        if 'description' in request.form and 'relationship_status' in request.form:
            description = request.form.get('description')
            relationship_status = request.form.get('relationship_status')
            city = request.form.get('city') if request.form.get('city') in RUSSIAN_CITIES else None
            cursor.execute("UPDATE users SET description = ?, relationship_status = ?, city = ? WHERE id = ?",
                         (description, relationship_status, city, user_id))
            conn.commit()
        elif 'post_content' in request.form:
            post_content = request.form['post_content']
            image = request.files.get('image')
            image_filename = None
            if image and allowed_file(image.filename):
                filename = secure_filename(image.filename)
                image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                image.save(image_path)
                image_filename = filename
            cursor.execute("INSERT INTO posts (user_id, content, image) VALUES (?, ?, ?)",
                         (user_id, post_content, image_filename))
            conn.commit()
            cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,))
            username = cursor.fetchone()[0]
            cursor.execute("SELECT created_at FROM posts WHERE id = LAST_INSERT_ROWID()")
            created_at = cursor.fetchone()[0]
            socketio.emit('new_post', {
                'username': username,
                'content': post_content,
                'image': image_filename,
                'created_at': created_at
            })
        elif 'avatar' in request.files:
            avatar = request.files['avatar']
            if avatar and allowed_file(avatar.filename):
                avatar_filename = secure_filename(avatar.filename)
                avatar_path = os.path.join(app.config['UPLOAD_FOLDER'], avatar_filename)
                avatar.save(avatar_path)
                cursor.execute("UPDATE users SET avatar = ? WHERE id = ?",
                             (avatar_filename, user_id))
                conn.commit()
    cursor.execute("SELECT username, description, relationship_status, avatar, city FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    cursor.execute("SELECT users.username FROM friends JOIN users ON friends.friend_id = users.id WHERE friends.user_id = ? AND friends.status = 'accepted'", (user_id,))
    friends = cursor.fetchall()
    cursor.execute("SELECT content, created_at, image FROM posts WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    posts = cursor.fetchall()
    conn.close()
    return render_template('profile.html', user=user, friends=friends, posts=posts, cities=RUSSIAN_CITIES)

@app.route('/create_chat/<int:friend_id>')
def create_chat(friend_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    if user_id == friend_id:
        flash('You cannot chat with yourself!', 'error')
        return redirect(url_for('home'))
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM chats WHERE (user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)",
                  (user_id, friend_id, friend_id, user_id))
    chat = cursor.fetchone()
    if not chat:
        cursor.execute("INSERT INTO chats (user1_id, user2_id) VALUES (?, ?)", (user_id, friend_id))
        conn.commit()
        cursor.execute("SELECT id FROM chats WHERE (user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)",
                      (user_id, friend_id, friend_id, user_id))
        chat = cursor.fetchone()
    conn.close()
    return redirect(url_for('chat', chat_id=chat[0]))

@app.route('/chat/<int:chat_id>')
def chat(chat_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user1_id, user2_id FROM chats WHERE id = ?", (chat_id,))
    chat = cursor.fetchone()
    if chat is None:
        return "Chat not found", 404
    friend_id = chat[0] if chat[1] == user_id else chat[1]
    cursor.execute("""
        UPDATE chat_messages 
        SET is_read = 1 
        WHERE chat_id = ? AND sender_id = ? AND is_read = 0
    """, (chat_id, friend_id))
    conn.commit()
    cursor.execute("""
        SELECT chats.id, users.username,
               (SELECT COUNT(*) FROM chat_messages 
                WHERE chat_messages.chat_id = chats.id 
                AND chat_messages.sender_id != ? 
                AND chat_messages.is_read = 0) AS unread_count
        FROM chats 
        JOIN users ON (chats.user1_id = users.id OR chats.user2_id = users.id)
        WHERE (chats.user1_id = ? OR chats.user2_id = ?) AND users.id != ?
    """, (user_id, user_id, user_id, user_id))
    chats = cursor.fetchall()
    cursor.execute("""
        SELECT users.username, chat_messages.content, chat_messages.created_at 
        FROM chat_messages
        JOIN users ON chat_messages.sender_id = users.id
        WHERE chat_messages.chat_id = ?
        ORDER BY chat_messages.created_at ASC
    """, (chat_id,))
    messages = cursor.fetchall()
    cursor.execute("SELECT username FROM users WHERE id = ?", (friend_id,))
    friend = cursor.fetchone()
    conn.close()
    send_notifications(user_id)
    return render_template('chat.html', chat_id=chat_id, friend=friend[0], messages=messages, user_id=user_id)

@app.route('/send_message/<int:chat_id>', methods=['POST'])
def send_message(chat_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    message = request.form['message']
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO chat_messages (chat_id, sender_id, content) VALUES (?, ?, ?)",
                  (chat_id, user_id, message))
    conn.commit()
    cursor.execute("SELECT created_at FROM chat_messages WHERE id = LAST_INSERT_ROWID()")
    created_at = cursor.fetchone()[0]
    cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,))
    sender_username = cursor.fetchone()[0]
    cursor.execute("SELECT user1_id, user2_id FROM chats WHERE id = ?", (chat_id,))
    chat = cursor.fetchone()
    receiver_id = chat[0] if chat[1] == user_id else chat[1]
    conn.close()
    message_data = {
        'chat_id': chat_id,
        'sender_id': user_id,
        'username': sender_username,
        'content': message,
        'created_at': created_at
    }
    socketio.emit('new_message', message_data, room=str(chat_id))
    send_notifications(receiver_id)
    return {'status': 'success', 'message': 'Message sent'}, 200

@app.route('/add_friend/<int:friend_id>')
def add_friend(friend_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM friends WHERE user_id = ? AND friend_id = ?", (user_id, friend_id))
    existing_friend = cursor.fetchone()
    if not existing_friend:
        cursor.execute("INSERT INTO friends (user_id, friend_id, status) VALUES (?, ?, 'pending')",
                      (user_id, friend_id))
        conn.commit()
        cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,))
        username = cursor.fetchone()[0]
        cursor.execute("SELECT created_at FROM friends WHERE user_id = ? AND friend_id = ?",
                      (user_id, friend_id))
        created_at = cursor.fetchone()[0]
        cursor.execute("INSERT INTO notifications (user_id, content, created_at) VALUES (?, ?, ?)",
                      (friend_id, f"{username} sent you a friend request", created_at))
        conn.commit()
        socketio.emit('new_friend_request', {
            'sender_id': user_id,
            'sender_username': username,
            'receiver_id': friend_id
        }, room=str(friend_id))
        send_notifications(friend_id)
    conn.close()
    return redirect(url_for('home'))

@app.route('/accept_friend/<int:friend_id>', methods=['POST'])
def accept_friend(friend_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM friends WHERE user_id = ? AND friend_id = ? AND status = 'pending'",
                  (friend_id, user_id))
    friend_request = cursor.fetchone()
    if friend_request:
        cursor.execute("UPDATE friends SET status = 'accepted' WHERE user_id = ? AND friend_id = ?",
                      (friend_id, user_id))
        cursor.execute("INSERT OR REPLACE INTO friends (user_id, friend_id, status) VALUES (?, ?, 'accepted')",
                      (user_id, friend_id))
        conn.commit()
        cursor.execute("SELECT username FROM users WHERE id = ?", (friend_id,))
        friend_username = cursor.fetchone()[0]
        cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,))
        accepter_username = cursor.fetchone()[0]
        cursor.execute("SELECT created_at FROM friends WHERE user_id = ? AND friend_id = ?",
                      (user_id, friend_id))
        created_at = cursor.fetchone()[0]
        cursor.execute("INSERT INTO notifications (user_id, content, created_at) VALUES (?, ?, ?)",
                      (friend_id, f"{accepter_username} accepted your friend request", created_at))
        conn.commit()
        socketio.emit('friend_request_accepted', {
            'user_id': user_id,
            'friend_id': friend_id,
            'friend_username': friend_username
        }, room=str(user_id))
        socketio.emit('friend_request_accepted', {
            'user_id': friend_id,
            'friend_id': user_id,
            'friend_username': accepter_username
        }, room=str(friend_id))
        send_notifications(friend_id)
        send_notifications(user_id)
    conn.close()
    return redirect(url_for('home'))

@app.route('/reject_friend/<int:friend_id>', methods=['POST'])
def reject_friend(friend_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM friends WHERE user_id = ? AND friend_id = ?", (friend_id, user_id))
    conn.commit()
    socketio.emit('friend_request_rejected', {
        'friend_id': friend_id
    }, room=str(user_id))
    send_notifications(user_id)
    conn.close()
    return redirect(url_for('home'))

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        description = request.form['description']
        city = request.form.get('city') if request.form.get('city') in RUSSIAN_CITIES else None
        if password != confirm_password:
            return 'Passwords do not match, please try again.'
        conn = sqlite3.connect('nana.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        existing_user = cursor.fetchone()
        if existing_user:
            conn.close()
            return 'Username already exists, please choose another one.'
        cursor.execute("INSERT INTO users (username, password, description, city) VALUES (?, ?, ?, ?)",
                      (username, password, description, city))
        conn.commit()
        conn.close()
        return redirect(url_for('login'))
    return render_template('register.html', cities=RUSSIAN_CITIES)

@app.route('/search_user', methods=['POST'])
def search_user():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    username = request.form['username']
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, relationship_status, avatar FROM users WHERE username LIKE ? AND id != ?",
                  (f'%{username}%', session['user_id']))
    users = cursor.fetchall()
    conn.close()
    # Отладка: вывести список файлов в папке templates
    templates_dir = os.path.join(os.path.dirname(__file__), 'templates')
    print("Templates directory:", templates_dir)
    print("Files in templates:", os.listdir(templates_dir))
    return render_template('search_result.html', users=users)

@app.errorhandler(jinja2.exceptions.TemplateNotFound)
def template_not_found(e):
    return jsonify({'error': f'Template not found: {str(e)}'}), 500

if __name__ == '__main__':
    init_db()
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=True, allow_unsafe_werkzeug=True)
