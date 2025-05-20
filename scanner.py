import discord
from discord.ext import tasks, commands
import asyncpraw
from dotenv import load_dotenv
import asyncio
import os
import logging
from collections import deque
from datetime import datetime
import sqlite3
import aiosqlite

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Configuration
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT")

# Reddit API credentials
reddit = asyncpraw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_CLIENT_SECRET,
    user_agent=REDDIT_USER_AGENT
)

# Discord bot setup with ALL required intents
intents = discord.Intents.all()  # Enable all intents
bot = commands.Bot(command_prefix="!", intents=intents)

# Your target subreddits and keywords
subreddits = ["FreeGameGiveaway", "IndianGaming", "pcgaming", "SteamGiveaways"]
keywords = ["giveaway", "free key", "steam key", "game key", "origin key", "giving away", "give away"]

# Use a deque with maxlen to limit memory usage
MAX_SEEN_POSTS = 1000
seen_posts = deque(maxlen=MAX_SEEN_POSTS)

# Database setup
async def setup_database():
    async with aiosqlite.connect('bot_data.db') as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS server_channels (
                server_id TEXT PRIMARY KEY,
                channel_id INTEGER NOT NULL
            )
        ''')
        await db.commit()

async def get_channel_id(server_id):
    async with aiosqlite.connect('bot_data.db') as db:
        async with db.execute(
            'SELECT channel_id FROM server_channels WHERE server_id = ?',
            (str(server_id),)
        ) as cursor:
            result = await cursor.fetchone()
            return result[0] if result else None

async def set_channel_id(server_id, channel_id):
    async with aiosqlite.connect('bot_data.db') as db:
        await db.execute(
            'INSERT OR REPLACE INTO server_channels (server_id, channel_id) VALUES (?, ?)',
            (str(server_id), channel_id)
        )
        await db.commit()

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user}")
    logger.info(f"Bot is in {len(bot.guilds)} guilds")
    await setup_database()
    check_reddit.start()

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command!")
    elif isinstance(error, commands.CommandNotFound):
        await ctx.send("❌ Command not found!")
    else:
        logger.error(f"Command error: {str(error)}")
        await ctx.send("❌ An error occurred while processing the command.")

@bot.command(name="setchannel")
@commands.has_permissions(administrator=True)
async def set_channel(ctx):
    """Set the channel for giveaway notifications"""
    server_id = str(ctx.guild.id)
    await set_channel_id(server_id, ctx.channel.id)
    await ctx.send(f"✅ Giveaway notifications will now be sent to this channel!")

@bot.command(name="settings")
@commands.has_permissions(administrator=True)
async def show_settings(ctx):
    """Show current bot settings"""
    server_id = str(ctx.guild.id)
    channel_id = await get_channel_id(server_id)
    channel = bot.get_channel(channel_id) if channel_id else None
    
    embed = discord.Embed(title="Bot Settings", color=discord.Color.blue())
    embed.add_field(name="Notification Channel", 
                   value=channel.mention if channel else "Not set", 
                   inline=False)
    embed.add_field(name="Monitored Subreddits", 
                   value="\n".join([f"r/{sub}" for sub in subreddits]), 
                   inline=False)
    embed.add_field(name="Keywords", 
                   value=", ".join(keywords), 
                   inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name="commands")
async def show_commands(ctx):
    """Show available commands"""
    embed = discord.Embed(title="Bot Commands", color=discord.Color.green())
    embed.add_field(name="!setchannel", 
                   value="Set the current channel for giveaway notifications (Admin only)", 
                   inline=False)
    embed.add_field(name="!settings", 
                   value="Show current bot settings (Admin only)", 
                   inline=False)
    embed.add_field(name="!commands", 
                   value="Show this help message", 
                   inline=False)
    
    await ctx.send(embed=embed)

@tasks.loop(seconds=30)
async def check_reddit():
    try:
        current_time = datetime.utcnow()
        one_hour_ago = current_time.timestamp() - 3600  # 3600 seconds = 1 hour

        # Get all servers and their channels
        async with aiosqlite.connect('bot_data.db') as db:
            async with db.execute('SELECT server_id, channel_id FROM server_channels') as cursor:
                server_channels = await cursor.fetchall()

        for server_id, channel_id in server_channels:
            channel = bot.get_channel(channel_id)
            if not channel:
                logger.error(f"Could not find channel with ID {channel_id} for server {server_id}")
                continue

            for sub in subreddits:
                try:
                    async with asyncio.timeout(30):  # 30 second timeout for each subreddit
                        subreddit = await reddit.subreddit(sub)
                        async for post in subreddit.new(limit=25):
                            if post.created_utc < one_hour_ago:
                                continue
                                
                            if post.id not in seen_posts:
                                title = post.title.lower()
                                if any(k in title for k in keywords):
                                    seen_posts.append(post.id)
                                    message = (
                                        f"🎁 **Giveaway Found!**\n"
                                        f"**Title:** {post.title}\n"
                                        f"**Link:** {post.url}\n"
                                        f"**Posted:** {datetime.fromtimestamp(post.created_utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                                        f"**Time Ago:** {int((current_time.timestamp() - post.created_utc) / 60)} minutes ago"
                                    )
                                    await channel.send(message)
                                    logger.info(f"Posted giveaway from r/{sub}: {post.title}")
                    
                    # Add delay between subreddit checks to respect rate limits
                    await asyncio.sleep(2)
                except asyncio.TimeoutError:
                    logger.error(f"Timeout while processing subreddit {sub}")
                    continue
                except Exception as e:
                    logger.error(f"Error processing subreddit {sub}: {str(e)}")
                    continue

    except Exception as e:
        logger.error(f"Error in check_reddit task: {str(e)}")

@check_reddit.before_loop
async def before_check_reddit():
    await bot.wait_until_ready()

# Run the bot
async def main():
    try:
        await bot.start(DISCORD_TOKEN)
    except Exception as e:
        logger.error(f"Failed to start bot: {str(e)}")
    finally:
        await reddit.close()

# Run the async main function
if __name__ == "__main__":
    asyncio.run(main())