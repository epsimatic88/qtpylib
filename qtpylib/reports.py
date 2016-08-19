#!/usr/bin/env python
# -*- coding: UTF-8 -*-

from flask import Flask, jsonify, request, make_response, send_from_directory
from flask import render_template
from flask.json import JSONEncoder

from dateutil.parser import parse as parse_date

import argparse
import datetime
import hashlib
import logging
import numpy as np
import pandas as pd
import tempfile
import pickle
import os
import sys
import pymysql
import glob

from qtpylib import path, tools

from qtpylib.blotter import Blotter

log = logging.getLogger('werkzeug')
log.setLevel(logging.WARNING)

# =============================================
# parse args
parser = argparse.ArgumentParser(description='QTPy Algo Framework')
parser.add_argument('--port', help='HTTP port to use (default: 5000)', required=False)
parser.add_argument('--blotter', help='Use this Blotter\'s MySQL server settings', required=False)
args, unknown = parser.parse_known_args()
# =============================================
#
#
class datetimeJSONEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime) | \
            isinstance(obj, datetime.date) | \
            isinstance(obj, datetime.time):
            return int(obj.strftime('%s'))
        return JSONEncoder.default(self, obj)


app = Flask(__name__, template_folder=path['library']+"/_webapp")
app.json_encoder = datetimeJSONEncoder


@app.template_filter('strftime')
def _jinja2_strftime(date, fmt=None):
    try: date = parse_date(date)
    except: pass
    native = date.replace(tzinfo=None)
    format='%Y-%m-%d %H:%M:%S%z'
    return native.strftime(format)


class Reports():
    """Reports class initilizer

    :Optional:

        blotter : str
            Log trades to MySQL server used by this Blotter (default is "auto detect")
        port : int
            HTTP port to use (default: 5000)
        host : string
            Host to bind the http process to (defaults 0.0.0.0)
    """

    # ---------------------------------------
    def __init__(self, blotter=None, port=5000, host="0.0.0.0"):
        # return
        self._password = hashlib.sha1(
            str(datetime.datetime.now()
        ).encode()).hexdigest()[:6]

        self.dbconn = None
        self.dbcurr = None

        self.host  = host
        self.port  = args.port if args.port is not None else port

        self.blotter_name = args.blotter if args.blotter is not None else blotter
        self.load_blotter_args(self.blotter_name)

        self.blotter = Blotter(**self.blotter_args)

    # ---------------------------------------
    def load_blotter_args(self, name=None):
        if name is not None:
            self.blotter_name = name

        # find specific name
        if self.blotter_name is not None: # and self.blotter_name != 'auto-detect':
            args_cache_file = tempfile.gettempdir()+"/"+self.blotter_name.lower()+".ezq"
            if not os.path.exists(args_cache_file):
                print("[ERROR] Cannot connect to running Blotter [%s]" % (self.blotter_name))
                sys.exit(0)

        # no name provided - connect to last running
        else:
            blotter_files = sorted(glob.glob(tempfile.gettempdir()+"/*.ezq"), key=os.path.getmtime)
            if len(blotter_files) == 0:
                print("[ERROR] Cannot connect to running Blotter [%s]" % (self.blotter_name))
                sys.exit(0)

            args_cache_file = blotter_files[-1]

        args = pickle.load( open(args_cache_file, "rb" ) )
        args['as_client'] = True

        if args:
            # connect to mysql
            self.dbconn = pymysql.connect(
                host   = str(args['dbhost']),
                port   = int(args['dbport']),
                user   = str(args['dbuser']),
                passwd = str(args['dbpass']),
                db     = str(args['dbname']),
                autocommit = True
            )
            self.dbcurr = self.dbconn.cursor()

        self.blotter_args = args

    # ---------------------------------------
    def send_static(self, path):
        return send_from_directory('_webapp/', path)

    # ---------------------------------------
    def login(self, password):
        if self._password == password:
            resp = make_response('yes')
            resp.set_cookie('password', password)
            return resp
        else:
            return make_response("no")


    # ---------------------------------------
    def algos(self, json=True):
        # if self._password != request.cookies.get('password'):
            # return render_template('login.html')

        algos = pd.read_sql("SELECT DISTINCT algo FROM trades", self.dbconn).to_dict(orient="records")

        if json:
            return jsonify(algos)
        else:
            return algos

    # ---------------------------------------
    def symbols(self, json=True):
        # if self._password != request.cookies.get('password'):
            # return render_template('login.html')

        symbols = pd.read_sql("SELECT * FROM symbols", self.dbconn).to_dict(orient="records")

        if json:
            return jsonify(symbols)
        else:
            return symbols

    # ---------------------------------------
    def trades(self, start=None, end=None, algo_id=None, json=True):
        # if self._password != request.cookies.get('password'):
            # return render_template('login.html')

        if algo_id is not None:
            algo_id = algo_id.replace('/', '')
        if start is not None:
            start = start.replace('/', '')
        if end is not None:
            end = end.replace('/', '')

        if start is None:
            start = tools.backdate("7D", date=None, as_datetime=False)

        trades_query = "SELECT * FROM trades WHERE exit_time IS NOT NULL"
        trades_where = []

        if isinstance(start, str):
            trades_where.append("entry_time>='"+start+"'")
        if isinstance(end, str):
            trades_where.append("exit_time<='"+end+"'")
        if algo_id is not None:
            trades_where.append("algo='"+algo_id+"'")

        if len(trades_where) > 0:
            trades_query += " AND "+" AND ".join(trades_where)

        trades  = pd.read_sql(trades_query, self.dbconn)
        trades['exit_time'].fillna(0, inplace=True)

        trades['slippage'] = abs(trades['entry_price']-trades['market_price'])

        trades['slippage'] = np.where(
            ( (trades['direction'] == "LONG") & (trades['entry_price'] > trades['market_price']) ) |
            ( (trades['direction'] == "SHORT") & (trades['entry_price'] < trades['market_price']) )
        , -trades['slippage'], trades['slippage'])

        trades = trades.sort_values(['exit_time', 'entry_time'], ascending=[False, False])

        trades = trades.to_dict(orient="records")
        if json:
            return jsonify(trades)
        else:
            return trades

    # ---------------------------------------
    def positions(self, algo_id=None, json=True):
        # if self._password != request.cookies.get('password'):
            # return render_template('login.html')

        if algo_id is not None:
            algo_id = algo_id.replace('/', '')

        trades_query = "SELECT * FROM trades WHERE exit_time IS NULL"
        if algo_id is not None:
            trades_query += " AND algo='"+algo_id+"'"

        trades  = pd.read_sql(trades_query, self.dbconn)


        last_query  = "SELECT s.id, s.symbol, max(t.last) as last_price FROM ticks t LEFT JOIN symbols s ON (s.id=t.symbol_id) GROUP BY s.id"
        last_prices = pd.read_sql(last_query, self.dbconn)

        trades = trades.merge(last_prices, on=['symbol'])

        trades['unrealized_pnl'] = np.where(
                trades['direction']=="SHORT",
                trades['entry_price']-trades['last_price'],
                trades['last_price']-trades['entry_price'])

        trades['slippage'] = abs(trades['entry_price']-trades['market_price'])
        trades['slippage'] = np.where(
            ( (trades['direction'] == "LONG") & (trades['entry_price'] > trades['market_price']) ) |
            ( (trades['direction'] == "SHORT") & (trades['entry_price'] < trades['market_price']) )
        , -trades['slippage'], trades['slippage'])

        trades = trades.sort_values(['entry_time'], ascending=[False])

        trades = trades.to_dict(orient="records")
        if json:
            return jsonify(trades)
        else:
            return trades

    # ---------------------------------------
    def trades_by_algo(self, algo_id=None, start=None, end=None):
        trades  = self.trades(start, end, algo_id=algo_id, json=False)
        return jsonify(trades)

    # ---------------------------------------
    def index(self, start=None, end=None):
        if self._password != request.cookies.get('password'):
            return render_template('login.html')

        return render_template('dashboard.html')


    # ---------------------------------------
    def bars(self, resolution, symbol, start=None, end=None, json=True):

        if start is not None:
            start = start.replace('/', '')
        if end is not None:
            end = end.replace('/', '')

        if start is None:
            start = tools.backdate("7D", date=None, as_datetime=False)

        bars = self.blotter.history(
            symbols    = symbol,
            start      = start,
            end        = end,
            resolution = resolution
        )

        bars['datetime'] = bars.index

        bars = bars.to_dict(orient="records")
        if json:
            return jsonify(bars)
        else:
            return bars


    # ---------------------------------------
    def run(self):
        """Starts the reporting module

        Makes the dashboard web app available via localhost:port, and exposes
        a REST API for trade information, open positions and market data.
        """

        global app

        # -----------------------------------
        # assign view
        app.add_url_rule('/', 'index', view_func=self.index)
        app.add_url_rule('/<path:start>', 'index', view_func=self.index)
        app.add_url_rule('/<start>/<path:end>', 'index', view_func=self.index)

        app.add_url_rule('/algos', 'algos', view_func=self.algos)
        app.add_url_rule('/symbols', 'symbols', view_func=self.symbols)

        app.add_url_rule('/positions', 'positions', view_func=self.positions)
        app.add_url_rule('/positions/<path:algo_id>', 'positions', view_func=self.positions)

        app.add_url_rule('/algo/<path:algo_id>', 'trades_by_algo', view_func=self.trades_by_algo)
        app.add_url_rule('/algo/<algo_id>/<path:start>', 'trades_by_algo', view_func=self.trades_by_algo)
        app.add_url_rule('/algo/<algo_id>/<start>/<path:end>', 'trades_by_algo', view_func=self.trades_by_algo)

        app.add_url_rule('/bars/<resolution>/<symbol>', 'bars', view_func=self.bars)
        app.add_url_rule('/bars/<resolution>/<symbol>/<path:start>', 'bars', view_func=self.bars)
        app.add_url_rule('/bars/<resolution>/<symbol>/<start>/<path:end>', 'bars', view_func=self.bars)

        app.add_url_rule('/trades', 'trades', view_func=self.trades)
        app.add_url_rule('/trades/<path:start>', 'trades', view_func=self.trades)
        app.add_url_rule('/trades/<start>/<path:end>', 'trades', view_func=self.trades)
        app.add_url_rule('/login/<password>', 'login', view_func=self.login)
        app.add_url_rule('/static/<path>', 'send_static', view_func=self.send_static)

        # let user know what the temp password is
        print(" * Web app password is:", self._password)

        # notice
        print(" * Running on http://"+ str(self.host) +":"+str(self.port)+"/ (Press CTRL+C to quit)")

        # -----------------------------------
        # run flask app
        app.run(
            # debug = True,
            host  = str(self.host),
            port  = int(self.port)
        )
