import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import json
import re
from collections import defaultdict
import random
import logging
import os
from typing import Optional, List, Dict, Any
import aiohttp
from io import BytesIO
import time
from bitarray import bitarray

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('StorkPuzzleBot')

class StorkPuzzle:
    def __init__(self, grid_size: int, num_words: int, channel_id: int):
        self.grid_size = grid_size
        self.num_words = num_words
        self.words = {}
        self.clues = {}
        self.images = {}
        self.current_image = "0" * num_words
        self.players = set()
        self.found_words = bitarray(num_words)
        self.found_words.setall(0)
        self.scores = defaultdict(int)
        self.channel_id = channel_id
        self.start_time = None
        self.end_time = None
        self.duration = None
        self.guessed_words = defaultdict(list)

    def add_word(self, word_num: int, word: str, clue: str):
        self.words[word_num] = word.lower()
        self.clues[word_num] = clue

    def check_word(self, word_num: int, guess: str) -> bool:
        if word_num not in self.words:
            return False
        if self.found_words[word_num - 1]:
            return False
        guess = re.sub(r'\W+', '', guess.lower())
        if guess == re.sub(r'\W+', '', self.words[word_num]):
            self.found_words[word_num - 1] = 1
            return True
        self.guessed_words[word_num].append(guess)
        return False

    def get_next_image_codes(self) -> List[str]:
        current = int(self.current_image, 2)
        return [format(current | (1 << i), f'0{self.num_words}b') for i in range(self.num_words) if not self.found_words[i]]

    def to_dict(self):
        return {
            "grid_size": self.grid_size,
            "num_words": self.num_words,
            "words": self.words,
            "clues": self.clues,
            "images": self.images,
            "current_image": self.current_image,
            "players": list(self.players),
            "found_words": self.found_words.tolist(),
            "scores": dict(self.scores),
            "channel_id": self.channel_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "guessed_words": dict(self.guessed_words)
        }

    @classmethod
    def from_dict(cls, data):
        game = cls(data["grid_size"], data["num_words"], data["channel_id"])
        game.words = data["words"]
        game.clues = data["clues"]
        game.images = data["images"]
        game.current_image = data["current_image"]
        game.players = set(data["players"])
        game.found_words = bitarray(data["found_words"])
        game.scores = defaultdict(int, data["scores"])
        game.start_time = data["start_time"]
        game.end_time = data["end_time"]
        game.duration = data["duration"]
        game.guessed_words = defaultdict(list, data["guessed_words"])
        return game

class StorkPuzzleBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix='/', intents=intents)
        self.games: Dict[int, StorkPuzzle] = {}
        self.setup_in_progress = set()
        self.command_permissions: Dict[str, List[int]] = {}

    async def setup_hook(self):
        await self.tree.sync()
        logger.info("Command tree synced")
        self.check_game_timers.start()

    async def on_ready(self):
        logger.info(f'{self.user} has connected to Discord!')
        await self.change_presence(activity=discord.Game(name="Stork Puzzle | /storkhelp"))
        self.load_games()
        self.load_command_permissions()

    def save_games(self):
        games_data = {str(channel_id): game.to_dict() for channel_id, game in self.games.items()}
        with open('stork_puzzle_saves.json', 'w') as f:
            json.dump(games_data, f)
        logger.info("Games saved successfully")

    def load_games(self):
        try:
            with open('stork_puzzle_saves.json', 'r') as f:
                games_data = json.load(f)
            for channel_id, game_data in games_data.items():
                self.games[int(channel_id)] = StorkPuzzle.from_dict(game_data)
            logger.info("Games loaded successfully")
        except FileNotFoundError:
            logger.info("No saved games found")
        except json.JSONDecodeError:
            logger.error("Error decoding saved games file")
        except KeyError as e:
            logger.error(f"Missing key in saved games file: {e}")

    def save_command_permissions(self):
        with open('command_permissions.json', 'w') as f:
            json.dump(self.command_permissions, f)
        logger.info("Command permissions saved successfully")

    def load_command_permissions(self):
        try:
            with open('command_permissions.json', 'r') as f:
                self.command_permissions = json.load(f)
            logger.info("Command permissions loaded successfully")
        except FileNotFoundError:
            logger.info("No saved command permissions found")
        except json.JSONDecodeError:
            logger.error("Error decoding command permissions file")

    @tasks.loop(minutes=1)
    async def check_game_timers(self):
        current_time = time.time()
        for channel_id, game in list(self.games.items()):
            if game.end_time and current_time >= game.end_time:
                asyncio.create_task(self.end_game_task(channel_id))

    async def end_game_task(self, channel_id: int):
        channel = self.get_channel(channel_id)
        if channel:
            await channel.send("Time's up! The Stork Puzzle game has ended.")
            await self.end_game(channel_id)

    async def end_game(self, channel_id: int):
        game = self.games.pop(channel_id, None)
        if game:
            self.save_games()
            channel = self.get_channel(channel_id)
            if channel:
                await channel.send(embed=self.create_leaderboard_embed(game))
                if "1" * game.num_words in game.images:
                    await channel.send(file=discord.File(BytesIO(game.images["1" * game.num_words]), filename="final_puzzle.png"))

    def create_leaderboard_embed(self, game: StorkPuzzle) -> discord.Embed:
        embed = discord.Embed(title="🏆 Stork Puzzle Leaderboard 🏆", color=0x00ff00)
        sorted_scores = sorted(game.scores.items(), key=lambda x: x[1], reverse=True)
        for i, (player_id, score) in enumerate(sorted_scores[:10], 1):
            player = self.get_user(player_id)
            embed.add_field(name=f"{i}. {player.name}", value=f"{score} word(s)", inline=False)
        return embed

bot = StorkPuzzleBot()

STORK_RESPONSES = {
    "welcome": [
        "Squawk! New puzzler in the nest! 🐣",
        "Flap-tastic to see you join! 🦅",
        "Egg-cellent choice, word warrior! 🥚",
    ],
    "correct_guess": [
        "You've cracked it! Egg-ceptional! 🥚💥",
        "Stork-pendous solving skills! 🦩",
        "Feather-tastic guess! You're soaring! 🕊️",
    ],
    "wrong_guess": [
        "Not quite hatched yet! Keep pecking! 🐣",
        "Ruffled feathers, but don't give up! 🪶",
        "This egg's still cooking! Try again! 🍳",
    ],
    "game_start": [
        "Storks assemble! Let the word hunt begin! 🦸‍♂️",
        "Puzzle eggs incoming! Prepare your beaks! 🥚☁️",
        "Nest of riddles now open! Dive in! 🏠",
    ],
    "hint": [
        "A little bird told me... 🐦",
        "Stork secret incoming! Listen closely... 🤫",
        "Nest whispers reveal... 🍃",
    ],
    "game_end": [
        "All eggs hatched! Puzzle complete! 🐣🎉",
        "Storks can rest their wings now. Great flight! 🦅💤",
        "Word nest emptied! Time for stork snacks! 🍽️",
    ]
}

def get_response(category: str) -> str:
    return random.choice(STORK_RESPONSES[category])

class SetupModal(discord.ui.Modal, title='Stork Puzzle Setup'):
    def __init__(self, num_words: int):
        super().__init__()
        self.grid_size = discord.ui.TextInput(label='Grid Size', placeholder='Enter a number (e.g., 5 for 5x5)')
        self.add_item(self.grid_size)
        for i in range(1, num_words + 1):
            self.add_item(discord.ui.TextInput(label=f'Word {i}', placeholder='Enter word'))
            self.add_item(discord.ui.TextInput(label=f'Clue {i}', placeholder='Enter clue'))

    async def on_submit(self, interaction: discord.Interaction):
        try:
            grid_size = int(self.grid_size.value)
            num_words = (len(self.children) - 1) // 2
            game = StorkPuzzle(grid_size, num_words, interaction.channel_id)

            for i in range(num_words):
                word = self.children[i*2 + 1].value
                clue = self.children[i*2 + 2].value
                game.add_word(i + 1, word.strip(), clue.strip())

            bot.games[interaction.channel_id] = game
            bot.save_games()

            await interaction.response.send_message("Game setup complete! Use `/upload_images` to add images, then `/startgame` to begin.", ephemeral=True)
        except ValueError as e:
            await interaction.response.send_message(f"Error in setup: {str(e)}", ephemeral=True)

class ImageUploadModal(discord.ui.Modal, title='Upload Stork Puzzle Images'):
    def __init__(self, game: StorkPuzzle):
        super().__init__()
        self.game = game
        self.add_item(discord.ui.TextInput(label='Empty Grid [0-0-0]', placeholder='Enter image URL or type "upload"'))
        self.add_item(discord.ui.TextInput(label='Complete Grid [1-1-1]', placeholder='Enter image URL or type "upload"'))
        for code in self.game.get_next_image_codes():
            if code != "0" * self.game.num_words and code != "1" * self.game.num_words:
                self.add_item(discord.ui.TextInput(label=f'Image [{code}]', placeholder='Enter image URL or type "upload"'))

    async def on_submit(self, interaction: discord.Interaction):
        for item in self.children:
            code = item.label.split('[')[1].split(']')[0].replace('-', '')
            if item.value.lower() == "upload":
                await interaction.response.send_message(f"Please upload the image for code {code}", ephemeral=True)
                try:
                    response = await bot.wait_for(
                        "message",
                        check=lambda m: m.author == interaction.user and m.channel == interaction.channel and m.attachments,
                        timeout=300.0
                    )
                    attachment = response.attachments[0]
                    image_data = await attachment.read()
                    self.game.images[code] = image_data
                except asyncio.TimeoutError:
                    await interaction.followup.send(f"Timeout: No image uploaded for code {code}", ephemeral=True)
            else:
                async with aiohttp.ClientSession() as session:
                    async with session.get(item.value) as resp:
                        if resp.status == 200:
                            image_data = await resp.read()
                            self.game.images[code] = image_data
                        else:
                            await interaction.followup.send(f"Failed to fetch image from URL for code {code}", ephemeral=True)

        bot.save_games()
        await interaction.followup.send("Images uploaded successfully!", ephemeral=True)

@bot.tree.command(name="setup", description="Start setting up a new Stork Puzzle game")
@app_commands.describe(num_words="Number of words in the puzzle")
async def setup_game(interaction: discord.Interaction, num_words: int):
    if interaction.channel_id in bot.setup_in_progress:
        await interaction.response.send_message("A setup is already in progress in this channel.", ephemeral=True)
        return

    bot.setup_in_progress.add(interaction.channel_id)
    await interaction.response.send_modal(SetupModal(num_words))
    bot.setup_in_progress.remove(interaction.channel_id)

@bot.tree.command(name="upload_images", description="Upload images for the Stork Puzzle game")
async def upload_images(interaction: discord.Interaction):
    game = bot.games.get(interaction.channel_id)
    if not game:
        await interaction.response.send_message("No game setup in this channel. Use `/setup` first.", ephemeral=True)
        return

    await interaction.response.send_modal(ImageUploadModal(game))

@bot.tree.command(name="startgame", description="Start the Stork Puzzle game")
@app_commands.describe(duration="Game duration in minutes (default: 60)")
async def start_game(interaction: discord.Interaction, duration: int = 60):
    game = bot.games.get(interaction.channel_id)
    if not game:
        await interaction.response.send_message("No game setup in this channel. Use `/setup` first.", 
                                                ephemeral=True)
        return

    if len(game.images) != 2**game.num_words:
        await interaction.response.send_message("Not all images have been uploaded. Use `/upload_images` to add them.",
                                                ephemeral=True)
        return

    game.start_time = time.time()
    game.duration = duration * 60
    game.end_time = game.start_time + game.duration

    await interaction.response.send_message(get_response("game_start"))
    await interaction.followup.send(f"Grid: {game.grid_size}x{game.grid_size}, Words: {game.num_words}")
    await interaction.followup.send("Clues:")
    for word_num, clue in game.clues.items():
        await interaction.followup.send(f"Word {word_num}: {clue}")
    
    if "0" * game.num_words in game.images:
        await interaction.followup.send(file=discord.File(BytesIO(game.images["0" * game.num_words]), filename="start_puzzle.png"))
    await interaction.followup.send(f"The game will end in {duration} minutes!")

    bot.save_games()

@bot.tree.command(name="join", description="Join the Stork Puzzle game")
async def join_game(interaction: discord.Interaction):
    game = bot.games.get(interaction.channel_id)
    if not game:
        await interaction.response.send_message("No active game in this channel!", ephemeral=True)
        return

    if interaction.user.id not in game.players:
        game.players.add(interaction.user.id)
        bot.save_games()
        await interaction.response.send_message(get_response("welcome"))
        if "0" * game.num_words in game.images:
            await interaction.followup.send(file=discord.File(BytesIO(game.images["0" * game.num_words]), filename="start_puzzle.png"))
    else:
        await interaction.response.send_message("You're already in the game!", ephemeral=True)

@bot.tree.command(name="guess", description="Make a guess in the Stork Puzzle game")
@app_commands.describe(
    word_num="The number of the word you're guessing",
    guess="Your guess for the word"
)
async def guess_word(interaction: discord.Interaction, word_num: int, guess: str):
    game = bot.games.get(interaction.channel_id)
    if not game:
        await interaction.response.send_message("No active game in this channel!", ephemeral=True)
        return

    if interaction.user.id not in game.players:
        await interaction.response.send_message("Join the game first with `/join`!", ephemeral=True)
        return

    if game.check_word(word_num, guess):
        game.scores[interaction.user.id] += 1
        new_image_code = list(game.current_image)
        new_image_code[word_num - 1] = '1'
        game.current_image = "".join(new_image_code)
        bot.save_games()
        await interaction.response.send_message(f"{get_response('correct_guess')} You've found word {word_num}!")
        if game.current_image in game.images:
            await interaction.followup.send(file=discord.File(BytesIO(game.images[game.current_image]), filename="puzzle.png"))
        
        if game.found_words.count() == game.num_words:
            await interaction.followup.send(get_response("game_end"))
            await bot.end_game(interaction.channel_id)
    else:
        await interaction.response.send_message(get_response("wrong_guess"), ephemeral=True)

@bot.tree.command(name="hint", description="Get a hint for a word in the Stork Puzzle game")
@app_commands.describe(word_num="The number of the word you want a hint for")
async def give_hint(interaction: discord.Interaction, word_num: int):
    game = bot.games.get(interaction.channel_id)
    if not game:
        await interaction.response.send_message("No active game in this channel!", ephemeral=True)
        return

    if word_num in game.clues:
        await interaction.response.send_message(f"{get_response('hint')} {game.clues[word_num]}", ephemeral=True)
    else:
        await interaction.response.send_message("Invalid word number!", ephemeral=True)

@bot.tree.command(name="game_status", description="Check the current game status")
async def game_status(interaction: discord.Interaction):
    game = bot.games.get(interaction.channel_id)
    if not game:
        await interaction.response.send_message("No active game in this channel!", ephemeral=True)
        return

    status = f"""
    **Game Status**
    Words Found: {game.found_words.count()}/{game.num_words}
    Players: {len(game.players)}
    Your Score: {game.scores[interaction.user.id]}
    """
    
    if game.end_time:
        remaining_time = max(0, int((game.end_time - time.time()) / 60))
        status += f"Time Remaining: {remaining_time} minutes\n"
    
    status += "Top 3 Players:\n"
    sorted_scores = sorted(game.scores.items(), key=lambda x: x[1], reverse=True)[:3]
    for i, (player_id, score) in enumerate(sorted_scores, 1):
        player = await bot.fetch_user(player_id)
        status += f"{i}. {player.name}: {score} word(s)\n"

    await interaction.response.send_message(status, ephemeral=True)

@bot.tree.command(name="leaderboard", description="View the current game leaderboard")
async def leaderboard(interaction: discord.Interaction):
    game = bot.games.get(interaction.channel_id)
    if not game:
        await interaction.response.send_message("No active game in this channel!", ephemeral=True)
        return

    embed = bot.create_leaderboard_embed(game)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="guessed_words", description="Show all guessed words")
async def show_guessed_words(interaction: discord.Interaction):
    game = bot.games.get(interaction.channel_id)
    if not game:
        await interaction.response.send_message("No active game in this channel!", ephemeral=True)
        return

    guessed_words_text = "**Guessed Words:**\n"
    for word_num, guesses in game.guessed_words.items():
        guessed_words_text += f"Word {word_num}: {', '.join(guesses)}\n"

    await interaction.response.send_message(guessed_words_text, ephemeral=True)

@bot.tree.command(name="storkhelp", description="Get help with Stork Puzzle commands")
async def stork_help(interaction: discord.Interaction):
    help_text = """
    🐣 **Stork Puzzle Commands** 🐣
    
    /setup <num_words> - Build a new nest
    /upload_images - Add images to the puzzle
    /startgame [duration] - Let the flock fly!
    /join - Fly into the game!
    /guess <word_num> <guess> - Crack an egg!
    /hint <word_num> - Get a clue!
    /game_status - Check your progress
    /leaderboard - See who's leading the flock
    /guessed_words - Show all guessed words
    
    Happy puzzling, storks!
    """
    await interaction.response.send_message(help_text)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.CommandOnCooldown):
        await interaction.response.send_message(f"Whoa, speedy stork! Try again in {error.retry_after:.2f} seconds.", ephemeral=True)
    else:
        logger.error(f"Unexpected error: {str(error)}", exc_info=True)
        await interaction.response.send_message("Oops! A gust of wind knocked us off course. Try again!", ephemeral=True)

token = os.getenv('DISCORD_BOT_TOKEN')
if token is None:
    token = "YOUR_BOT_TOKEN_HERE"  # Replace with your actual bot token

if token == "YOUR_BOT_TOKEN_HERE":
    print("WARNING: You are using a placeholder token. Please replace it with your actual bot token.")

bot.run(token)