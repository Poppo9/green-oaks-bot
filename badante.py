import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
import os

load_dotenv()
token = os.getenv('DISCORD_TOKEN')

handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f"SUCCESS: {bot.user.name} avviato correttamente!")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if "popo" in message.content.lower():
        await message.delete()
        await message.channel.send(f"{message.author.mention} ha detto popo.")
    
    await bot.process_commands(message)

@bot.command()
async def hello(ctx):

    await ctx.send(f"ciao {ctx.author.mention}")
    

@bot.command
async def e(ctx):
    await ctx.send("Hai estratto una carta!")


bot.run(token, log_handler=handler, log_level=logging.INFO)