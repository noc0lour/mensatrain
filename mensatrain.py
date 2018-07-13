#!/usr/bin/env python3
import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters

import sqlalchemy
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.pool import NullPool

import dateutil.parser
import datetime
import tabulate
import argparse

import json
import inspect
import logging

Base = declarative_base()

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)

logger = logging.getLogger(__name__)

# Define a few command handlers. These usually take the two arguments bot and
# update. Error handlers also receive the raised TelegramError object in error.


class ScheduleMap(Base):
    __tablename__ = 'schedule'
    id = sqlalchemy.Column(
        sqlalchemy.Integer, primary_key=True, autoincrement=True)
    date = sqlalchemy.Column(sqlalchemy.DateTime)
    station = sqlalchemy.Column(sqlalchemy.String(50))
    tickets = relationship("TicketMap", back_populates="journey")
    owner = sqlalchemy.Column(sqlalchemy.Integer,
                              sqlalchemy.ForeignKey("users.id"))
    valid = sqlalchemy.Column(sqlalchemy.Boolean, default=True)


class TicketMap(Base):
    __tablename__ = 'tickets'
    id = sqlalchemy.Column(
        sqlalchemy.Integer, primary_key=True, autoincrement=True)
    sid = sqlalchemy.Column(sqlalchemy.Integer,
                            sqlalchemy.ForeignKey('schedule.id'))
    journey = relationship("ScheduleMap", back_populates="tickets")
    uid = sqlalchemy.Column(sqlalchemy.Integer,
                            sqlalchemy.ForeignKey("users.id"))
    user = relationship("UserMap", back_populates="tickets")
    valid = sqlalchemy.Column(sqlalchemy.Boolean, default=True)


class UserMap(Base):
    __tablename__ = 'users'
    id = sqlalchemy.Column(
        sqlalchemy.Integer, primary_key=True, autoincrement=True)
    tid = sqlalchemy.Column(sqlalchemy.String(32))
    username = sqlalchemy.Column(sqlalchemy.String(32))
    fullname = sqlalchemy.Column(sqlalchemy.String(50))
    tickets = relationship("TicketMap", back_populates="user")


class MensaTrainBot(object):
    def __init__(self, db_file):
        engine = sqlalchemy.create_engine(
            'sqlite:///{}'.format(db_file), poolclass=NullPool)
        Base.metadata.create_all(engine, checkfirst=True)
        self.session = sessionmaker(bind=engine)

    def help(self, bot, update):
        """Send a message when the command /help is issued."""
        update.message.reply_text(
            inspect.cleandoc(
                """The MensaTrainBot will help you planning your daily MensaTrain.
            /schedule will display today's schedule
            /add_departure $local_time $station_name adds a new departure to today's schedule
            /ticket ($local_time $station_name) will get you a ticket to a MensaTrain if it exists
            /revoke will revoke your current ticket for a train of the day"""))

    def parse_args(self, args):
        if args is None or len(args) < 2:
            return (
                None,
                "Not enough arguments. Please provide time and station name.")
        try:
            date = dateutil.parser.parse(str(args[0]))
        except ValueError or OverflowError as err:
            return (None, "Invalid date format")

        if not datetime.date.today() == date.date():
            return (None,
                    "Schedule planning is only supported for the same day")

        if (date.hour > 14) or (date.hour == 14 and date.minute > 30) or (
                date.hour < 11):
            return (None, "Schedule planning only possible from 11:00 until 14:30")

        return (date, args[1])

    def get_user(self, update):
        user_id = update.effective_user.id
        session = self.session()
        user = session.query(UserMap).filter_by(tid=user_id)
        if user.count() == 0:
            session.add(
                UserMap(
                    tid=user_id,
                    username=update.effective_user.username,
                    fullname=update.effective_user.full_name))
            session.commit()
            user = session.query(UserMap).filter_by(tid=user_id)
        return user.one()

    def get_user_journey(self, update):
        session = self.session()
        user_id = update.effective_user.id
        # Check if the user already has a ticket for today
        user_journeys = session.query(UserMap).filter_by(
            tid=user_id).join(TicketMap).join(ScheduleMap).filter(
                ScheduleMap.date > datetime.date.today(), ScheduleMap.date <
                datetime.date.today() + datetime.timedelta(1),
                TicketMap.valid == True)
        return user_journeys.one_or_none()

    def get_user_ticket(self, update):
        session = self.session()
        user_id = update.effective_user.id
        user_ticket = session.query(TicketMap).filter(
            TicketMap.valid == True).join(ScheduleMap).join(UserMap).filter(
                TicketMap.user.tid == user_id).filter(
                    TicketMap.journey.date > datetime.date.today(), TicketMap.journey.date
                    < datetime.date.today() + datetime.timedelta(1))
        return user_ticket.one_or_none()

    def build_keyboard(self, items):
        keyboard = [[item] for item in items]
        reply_markup = {"keyboard": keyboard, "one_time_keyboard": True}
        return json.dumps(reply_markup)

    def get_trains_today(self):
        session = self.session()
        schedule_information = []
        journeys = session.query(ScheduleMap).filter(
            ScheduleMap.date > datetime.date.today(),
            ScheduleMap.date < datetime.date.today() + datetime.timedelta(1),
            ScheduleMap.valid == True).order_by(ScheduleMap.date)
        for j in journeys:
            participants = session.query(TicketMap).filter(
                TicketMap.sid == j.id,
                TicketMap.valid == True).join(UserMap).all()  # noqa
            schedule_information.append([
                j.id, ":".join((str(j.date.hour), str(j.date.minute))),
                j.station, ", ".join([
                    "[" + p.user.fullname + "](tg://user?id=" + p.user.tid +
                    ")" for p in participants
                ])
            ])
        return schedule_information

    def schedule(self, bot, update):
        """
        """
        schedule_information = self.get_trains_today()
        schedule_information_text = "Trains scheduled today:\n"
        schedule_information_headers = [
            "ID", "Departure", "Station", "Passengers"
        ]
        schedule_information_text += tabulate.tabulate(
            schedule_information,
            schedule_information_headers,
            tablefmt="simple")
        update.message.reply_markdown(schedule_information_text)

    def ticket(self, bot, update, **kwargs):
        """
        """
        session = self.session()
        user_id = update.effective_user.id
        # Check if the user already has a ticket for today
        # user_journey = self.get_user_journey(update)
        user_ticket = self.get_user_ticket(update)
        if user_ticket is not None:
            update.message.reply_text(
                "Error processing your request: Already registered for a train today."
            )
            return

        args = self.parse_args(kwargs.get('args', None))
        if args[0] is None:
            schedule = self.get_trains_today()
            custom_keyboard = [["/ticket " + t[1] + " " + t[2]]
                               for t in schedule]
            reply_markup = telegram.ReplyKeyboardMarkup(
                custom_keyboard, one_time_keyboard=True, selective=True)
            update.message.reply_text(
                "Select an available train.", reply_markup=reply_markup)
            return
        journeys = session.query(ScheduleMap).filter_by(
            date=args[0], station=args[1])
        if journeys.count() == 0:
            update.message.reply_text(
                "Error processing your request: No valid journey found.")
            return

        if journeys.count() > 1:
            update.message.reply_text(
                "Error processing your request: Duplicate journeys found.")
            return

        user = self.get_user(update)
        journey = journeys.one()
        session.add(TicketMap(sid=journey.id, uid=user.id))
        session.commit()
        update.message.reply_text(
            "You successfully bought your ticket for the train departing from {} at {}".
            format(journey.station, ":".join((str(journey.date.hour), str(
                journey.date.minute)))))

    def revoke(self, bot, update):
        session = self.session()
        user_ticket = self.get_user_ticket(update)
        if user_ticket is None:
            update.message.reply_text(
                "Error processing your request: No ticket available to revoke")
            return
        ticket = session.query(TicketMap).filter_by(id=user_ticket.id).one()
        ticket.valid = False
        update.message.reply_text(
            "Successfully revoked your Ticket for the journey at {} from {}.".
            format(":".join((str(ticket.journey.date.hour), str(
                ticket.journey.date.minute))), ticket.journey.station))
        session.commit()

        return

    def add_departure(self, bot, update, **kwargs):
        """
        """
        args = self.parse_args(kwargs.get('args', None))
        if args[0] is None:
            update.message.reply_text(
                "Error processing your request: {}".format(args[1]))
            return
        user = self.get_user(update)
        session = self.session()
        session.add(ScheduleMap(date=args[0], station=args[1], owner=user.id))
        session.commit()
        self.ticket(bot, update, args=args)

    def error(self, bot, update, error):
        """Log Errors caused by Updates."""
        logger.warning('Update "%s" caused error "%s"', update, error)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-t', '--token', help='Telegram bot API token. Keep this sekrit!')
    return parser.parse_args()


def main():
    """Start the bot."""
    # Create the EventHandler and pass it your bot's token.
    args = parse_args()
    updater = Updater(args.token)

    # Get the dispatcher to register handlers
    dp = updater.dispatcher

    mybot = MensaTrainBot("fahrplan.db")

    # on different commands - answer in Telegram
    dp.add_handler(
        CommandHandler(
            "add_departure",
            mybot.add_departure,
            pass_args=True,
            pass_user_data=True))
    dp.add_handler(
        CommandHandler(
            "ticket", mybot.ticket, pass_args=True, pass_user_data=True))
    dp.add_handler(CommandHandler("schedule", mybot.schedule))
    dp.add_handler(CommandHandler("revoke", mybot.revoke))
    dp.add_handler(CommandHandler("help", mybot.help))
    dp.add_handler(CommandHandler("start", mybot.help))

    # on noncommand i.e message - echo the message on Telegram
    # dp.add_handler(MessageHandler(Filters.text, mybot.echo))

    # log all errors
    dp.add_error_handler(mybot.error)

    # Start the Bot
    updater.start_polling()

    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()


if __name__ == '__main__':
    main()
