from telegram_notify import TelegramNotifier

bot = TelegramNotifier().start()

bot.send_message("🐦 BirdWatch AI подключён!")

bot.tasks.join()
bot.stop()