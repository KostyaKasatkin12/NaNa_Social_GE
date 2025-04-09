from flask import Flask, render_template, redirect, url_for, request, session, flash
from flask_socketio import SocketIO, emit, join_room
import sqlite3
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'your_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*")  # Разрешаем CORS для Replit

UPLOAD_FOLDER = 'static/avatars'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def init_db():
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    # Существующие таблицы
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        password TEXT NOT NULL,
        description TEXT,
        relationship_status TEXT DEFAULT 'не интересуюсь',
        avatar TEXT
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
    # Обновляем таблицу chat_messages, добавляем поле is_read
    cursor.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_read INTEGER DEFAULT 0,  -- 0: не прочитано, 1: прочитано
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
    # Проверяем, есть ли поле is_read в chat_messages, если нет — добавляем
    cursor.execute("PRAGMA table_info(chat_messages)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'is_read' not in columns:
        cursor.execute("ALTER TABLE chat_messages ADD COLUMN is_read INTEGER DEFAULT 0")
        cursor.execute("UPDATE chat_messages SET is_read = 0 WHERE is_read IS NULL")
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
    # Посты
    cursor.execute("""
        SELECT posts.id, posts.content, posts.created_at, users.username, posts.image,
               (SELECT COUNT(*) FROM post_reactions WHERE post_id = posts.id AND reaction = 'like') AS likes,
               (SELECT COUNT(*) FROM post_reactions WHERE post_id = posts.id AND reaction = 'dislike') AS dislikes,
               (SELECT reaction FROM post_reactions WHERE post_id = posts.id AND user_id = ?) AS user_reaction
        FROM posts 
        JOIN users ON posts.user_id = users.id 
        ORDER BY posts.created_at DESC
    """, (user_id,))
    posts = cursor.fetchall()
    # Друзья
    cursor.execute("""
        SELECT users.username, users.id FROM friends 
        JOIN users ON friends.friend_id = users.id
        WHERE friends.user_id = ? AND friends.status = 'accepted'
    """, (user_id,))
    friends = cursor.fetchall()
    # Запросы на дружбу
    cursor.execute("""
        SELECT users.id, users.username FROM friends 
        JOIN users ON friends.user_id = users.id
        WHERE friends.friend_id = ? AND friends.status = 'pending'
    """, (user_id,))
    friend_requests = cursor.fetchall()
    # Чаты с количеством непрочитанных сообщений
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
    # Уведомления о непрочитанных сообщениях
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
    # Формируем уведомления в формате {имя пользователя}: {кол-во непрочитанных}
    notifications = []
    for username, unread_count in unread_notifications:
        notifications.append((f"{username}: {unread_count}", None))  # created_at не нужен, оставляем None
    # Добавляем остальные уведомления из таблицы notifications
    cursor.execute("SELECT content, created_at FROM notifications WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    notifications.extend(cursor.fetchall())
    conn.close()
    if user:
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
    # Проверяем, есть ли уже реакция от пользователя
    cursor.execute("SELECT reaction FROM post_reactions WHERE post_id = ? AND user_id = ?", (post_id, user_id))
    existing_reaction = cursor.fetchone()
    if existing_reaction:
        if existing_reaction[0] == 'like':
            # Если уже лайк, убираем реакцию
            cursor.execute("DELETE FROM post_reactions WHERE post_id = ? AND user_id = ?", (post_id, user_id))
        else:
            # Если был дизлайк, меняем на лайк
            cursor.execute("UPDATE post_reactions SET reaction = 'like' WHERE post_id = ? AND user_id = ?",
                          (post_id, user_id))
    else:
        # Если реакции не было, добавляем лайк
        cursor.execute("INSERT INTO post_reactions (post_id, user_id, reaction) VALUES (?, ?, 'like')",
                      (post_id, user_id))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('home'))

@app.route('/dislike_post/<int:post_id>', methods=['POST'])
def dislike_post(post_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = sqlite3.connect('nana.db')
    cursor = conn.cursor()
    # Проверяем, есть ли уже реакция от пользователя
    cursor.execute("SELECT reaction FROM post_reactions WHERE post_id = ? AND user_id = ?", (post_id, user_id))
    existing_reaction = cursor.fetchone()
    if existing_reaction:
        if existing_reaction[0] == 'dislike':
            # Если уже дизлайк, убираем реакцию
            cursor.execute("DELETE FROM post_reactions WHERE post_id = ? AND user_id = ?", (post_id, user_id))
        else:
            # Если был лайк, меняем на дизлайк
            cursor.execute("UPDATE post_reactions SET reaction = 'dislike' WHERE post_id = ? AND user_id = ?",
                          (post_id, user_id))
    else:
        # Если реакции не было, добавляем дизлайк
        cursor.execute("INSERT INTO post_reactions (post_id, user_id, reaction) VALUES (?, ?, 'dislike')",
                      (post_id, user_id))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('home'))

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
    cursor.execute("SELECT created_at FROM posts WHERE id = LAST_INSERT_ROWID()")
    created_at = cursor.fetchone()[0]
    conn.close()
    socketio.emit('new_post', {
        'username': username,
        'content': content,
        'image': image_filename,
        'created_at': created_at
    })
    return redirect(url_for('home'))

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
            cursor.execute("UPDATE users SET description = ?, relationship_status = ? WHERE id = ?",
                         (description, relationship_status, user_id))
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
    cursor.execute("SELECT username, description, relationship_status, avatar FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    cursor.execute("SELECT users.username FROM friends JOIN users ON friends.friend_id = users.id WHERE friends.user_id = ? AND friends.status = 'accepted'", (user_id,))
    friends = cursor.fetchall()
    cursor.execute("SELECT content, created_at, image FROM posts WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    posts = cursor.fetchall()
    conn.close()
    return render_template('profile.html', user=user, friends=friends, posts=posts)

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
    # Отмечаем все сообщения от собеседника как прочитанные
    cursor.execute("""
        UPDATE chat_messages 
        SET is_read = 1 
        WHERE chat_id = ? AND sender_id = ? AND is_read = 0
    """, (chat_id, friend_id))
    conn.commit()
    # Получаем обновлённое количество непрочитанных сообщений для всех чатов
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
    # Получаем обновлённые уведомления о непрочитанных сообщениях
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
    notifications = [(f"{username}: {unread_count}", None) for username, unread_count in unread_notifications]
    # Получаем сообщения чата
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
    # Отправляем событие через SocketIO для обновления Chats и Notifications
    socketio.emit('update_unread', {
        'user_id': user_id,
        'chats': [{'chat_id': chat[0], 'username': chat[1], 'unread_count': chat[2]} for chat in chats],
        'notifications': notifications
    }, room=str(user_id))
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
    conn.close()
    message_data = {
        'chat_id': chat_id,
        'sender_id': user_id,
        'username': sender_username,
        'content': message,
        'created_at': created_at
    }
    print(f"Sending message via SocketIO: {message_data}")
    socketio.emit('new_message', message_data, room=str(chat_id))
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
        conn.close()
        socketio.emit('new_friend_request', {
            'sender_id': user_id,
            'receiver_id': friend_id,
            'username': username,
            'created_at': created_at
        }, room=str(friend_id))
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
        conn.close()
        socketio.emit('friend_accepted', {
            'user_id': user_id,
            'friend_id': friend_id,
            'username': friend_username,
            'created_at': created_at
        }, room=str(friend_id))
        socketio.emit('friend_accepted', {
            'user_id': friend_id,
            'friend_id': user_id,
            'username': accepter_username,
            'created_at': created_at
        }, room=str(user_id))
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
        if password != confirm_password:
            return 'Passwords do not match, please try again.'
        conn = sqlite3.connect('nana.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        existing_user = cursor.fetchone()
        if existing_user:
            conn.close()
            return 'Username already exists, please choose another one.'
        cursor.execute("INSERT INTO users (username, password, description) VALUES (?, ?, ?)",
                      (username, password, description))
        conn.commit()
        conn.close()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/search_user', methods=['GET', 'POST'])
def search_user():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    if request.method == 'POST':
        username = request.form['username']
        conn = sqlite3.connect('nana.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, description, relationship_status, avatar FROM users WHERE username = ?",
                      (username,))
        user = cursor.fetchone()
        if user:
            cursor.execute("SELECT status FROM friends WHERE user_id = ? AND friend_id = ?",
                          (user_id, user[0]))
            friendship_status = cursor.fetchone()
        else:
            friendship_status = None
        conn.close()
        if user:
            return render_template('search_user.html', user=user, friendship_status=friendship_status)
        else:
            return render_template('search_user.html', error="User not found")
    return render_template('search_user.html')


if __name__ == '__main__':
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    init_db()
    port = int(os.environ.get("PORT", 8080))
    socketio.run(app, host="0.0.0.0", port=port, debug=True, allow_unsafe_werkzeug=True)
