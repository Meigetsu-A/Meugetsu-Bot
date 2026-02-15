import os
import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import asyncio
import logging
import threading
from flask import Flask, render_template_string, request, redirect, url_for, session
from dotenv import load_dotenv
import google.generativeai as genai
import datetime
import json
import re

# Load environment variables
load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_KEY = os.getenv('GEMINI_API_KEY')
DASH_PORT = int(os.getenv('DASHBOARD_PORT', 5000))
DASH_PASS = os.getenv('DASHBOARD_PASSWORD', 'admin')
FLASK_SECRET = os.getenv('FLASK_SECRET_KEY', 'default_secret_key')

# Discord Bot Setup
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

# Database Initialization
def init_db():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS settings
                 (guild_id INTEGER PRIMARY KEY,
                  mod_log_channel INTEGER,
                  anti_raid_enabled INTEGER DEFAULT 0,
                  automod_enabled INTEGER DEFAULT 0)''')

    c.execute('''CREATE TABLE IF NOT EXISTS forensic_logs
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  guild_id INTEGER,
                  user_id INTEGER,
                  channel_id INTEGER,
                  event_type TEXT,
                  content TEXT,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')

    c.execute('''CREATE TABLE IF NOT EXISTS cases
                 (case_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  guild_id INTEGER,
                  user_id INTEGER,
                  mod_id INTEGER,
                  action TEXT,
                  reason TEXT,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

# Helper function to get settings
def get_settings(guild_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT * FROM settings WHERE guild_id = ?", (guild_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {'mod_log': row[1], 'anti_raid': row[2], 'automod': row[3]}
    return None

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"Sync error: {e}")

# Moderation
@bot.tree.command(name="kick")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.kick(reason=reason)
    await interaction.response.send_message(f'Kicked {member.mention}')

@bot.tree.command(name="ban")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.ban(reason=reason)
    await interaction.response.send_message(f'Banned {member.mention}')

# Anti-Raid
join_tracker = {}
@bot.event
async def on_member_join(member):
    settings = get_settings(member.guild.id)
    if settings and settings['anti_raid']:
        now = datetime.datetime.now()
        tracker = join_tracker.get(member.guild.id, [])
        tracker = [t for t in tracker if (now - t).total_seconds() < 10]
        tracker.append(now)
        join_tracker[member.guild.id] = tracker
        if len(tracker) > 5:
            log_chan = member.guild.get_channel(settings['mod_log'])
            if log_chan: await log_chan.send("⚠️ Mass join detected!")

# Forensic Logging
@bot.event
async def on_message_delete(message):
    if message.author.bot: return
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("INSERT INTO forensic_logs (guild_id, user_id, channel_id, event_type, content) VALUES (?, ?, ?, ?, ?)",
              (message.guild.id, message.author.id, message.channel.id, "DELETE", message.content))
    conn.commit()
    conn.close()

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or before.content == after.content: return
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("INSERT INTO forensic_logs (guild_id, user_id, channel_id, event_type, content) VALUES (?, ?, ?, ?, ?)",
              (before.guild.id, before.author.id, before.channel.id, "EDIT", f"B: {before.content} | A: {after.content}"))
    conn.commit()
    conn.close()

# AI Assistant
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')

@bot.tree.command(name="ai_chat")
async def ai_chat(interaction: discord.Interaction, prompt: str):
    if not GEMINI_KEY: return await interaction.response.send_message("AI not configured")
    await interaction.response.defer()
    try:
        response = model.generate_content(prompt)
        await interaction.followup.send(response.text[:2000])
    except Exception as e:
        await interaction.followup.send(f"AI Error: {e}")

# Dashboard
app = Flask(__name__)
app.secret_key = FLASK_SECRET

@app.route('/')
def index():
    return render_template_string("""
    <h1>Bot Dashboard</h1>
    {% if session.logged_in %}
        <p>Status: Online</p>
        <a href="/logout">Logout</a>
    {% else %}
        <form method="post" action="/login"><input type="password" name="password"><button type="submit">Login</button></form>
    {% endif %}
    """, session=session)

@app.route('/login', methods=['POST'])
def login():
    if request.form.get('password') == DASH_PASS: session['logged_in'] = True
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('index'))

def run_dash():
    app.run(host='0.0.0.0', port=DASH_PORT)

threading.Thread(target=run_dash, daemon=True).start()

if TOKEN:
    bot.run(TOKEN)
