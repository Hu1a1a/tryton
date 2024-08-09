# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
import datetime
import logging
import time

from dateutil.relativedelta import relativedelta
from sql import Literal
from sql.conditionals import Coalesce

from trytond import backend
from trytond.config import config
from trytond.exceptions import UserError, UserWarning
from trytond.model import (
    DeactivableMixin, Index, ModelSQL, ModelView, dualmethod, fields)
from trytond.pool import Pool
from trytond.pyson import Eval
from trytond.status import processing
from trytond.tools import timezone as tz
from trytond.transaction import Transaction, TransactionError
from trytond.worker import run_task

logger = logging.getLogger(__name__)


class Cron(DeactivableMixin, ModelSQL, ModelView):
    "Cron"
    __name__ = "ir.cron"
    interval_number = fields.Integer('Interval Number', required=True)
    interval_type = fields.Selection([
            ('minutes', 'Minutes'),
            ('hours', 'Hours'),
            ('days', 'Days'),
            ('weeks', 'Weeks'),
            ('months', 'Months'),
            ], "Interval Type", sort=False, required=True)
    minute = fields.Integer("Minute",
        domain=['OR',
            ('minute', '=', None),
            [('minute', '>=', 0), ('minute', '<=', 59)],
            ],
        states={
            'invisible': Eval('interval_type').in_(['minutes']),
            },
        depends=['interval_type'])
    hour = fields.Integer("Hour",
        domain=['OR',
            ('hour', '=', None),
            [('hour', '>=', 0), ('hour', '<=', 23)],
            ],
        states={
            'invisible': Eval('interval_type').in_(['minutes', 'hours']),
            },
        depends=['interval_type'])
    weekday = fields.Many2One(
        'ir.calendar.day', "Day of Week",
        states={
            'invisible': Eval('interval_type').in_(
                ['minutes', 'hours', 'days']),
            },
        depends=['interval_type'])
    day = fields.Integer("Day",
        domain=['OR',
            ('day', '=', None),
            ('day', '>=', 0),
            ],
        states={
            'invisible': Eval('interval_type').in_(
                ['minutes', 'hours', 'days', 'weeks']),
            },
        depends=['interval_type'])
    timezone = fields.Function(fields.Char("Timezone"), 'get_timezone')

    next_call = fields.DateTime("Next Call")
    method = fields.Selection([
            ('ir.trigger|trigger_time', "Run On Time Triggers"),
            ('ir.queue|clean', "Clean Task Queue"),
            ('ir.error|clean', "Clean Errors"),
            ], "Method", required=True)

    @classmethod
    def __setup__(cls):
        super(Cron, cls).__setup__()
        table = cls.__table__()

        cls._buttons.update({
                'run_once': {
                    'icon': 'tryton-launch',
                    },
                })
        cls._sql_indexes.add(Index(table, (table.next_call, Index.Range())))

    @classmethod
    def __register__(cls, module_name):
        super().__register__(module_name)

        table_h = cls.__table_handler__(module_name)

        # Migration from 5.0: remove fields
        for column in ['name', 'user', 'request_user', 'number_calls',
                'repeat_missed', 'model', 'function', 'args']:
            table_h.drop_column(column)

        # Migration from 5.0: remove required on next_call
        table_h.not_null_action('next_call', 'remove')

    @classmethod
    def default_timezone(cls):
        return tz.SERVER.tzname(datetime.datetime.now())

    def get_timezone(self, name):
        return self.default_timezone()

    @classmethod
    def check_xml_record(cls, crons, values):
        pass

    @classmethod
    def view_attributes(cls):
        return [(
                '//label[@id="time_label"]', 'states', {
                    'invisible': Eval('interval_type') == 'minutes',
                }),
            ]

    def compute_next_call(self, now=None):
        if now is None:
            now = datetime.datetime.now()
        return (now.replace(tzinfo=tz.UTC).astimezone(tz.SERVER)
            + relativedelta(**{self.interval_type: self.interval_number})
            + relativedelta(
                microsecond=0,
                second=0,
                minute=(
                    self.minute
                    if self.interval_type != 'minutes'
                    else None),
                hour=(
                    self.hour
                    if self.interval_type not in {'minutes', 'hours'}
                    else None),
                day=(
                    self.day
                    if self.interval_type not in {
                        'minutes', 'hours', 'days', 'weeks'}
                    else None),
                weekday=(
                    int(self.weekday.index)
                    if self.weekday
                    and self.interval_type not in {'minutes', 'hours', 'days'}
                    else None))).astimezone(tz.UTC).replace(tzinfo=None)

    @dualmethod
    @ModelView.button
    def run_once(cls, crons):
        pool = Pool()
        for cron in crons:
            model, method = cron.method.split('|')
            Model = pool.get(model)
            getattr(Model, method)()

    @classmethod
    def run(cls, db_name):
        logger.info('cron started for "%s"', db_name)
        now = datetime.datetime.now()
        retry = config.getint('database', 'retry')
        count = 0
        current_task_id = None
        transaction_extras = {}
        skip_task_ids = [-1]
        while True:
            if count:
                time.sleep(0.02 * (retry - count))
            with Transaction().start(
                    db_name, 0, context={'_skip_warnings': True},
                    **transaction_extras) as transaction:
                pool = Pool()
                Error = pool.get('ir.error')
                table = cls.__table__()
                database = transaction.database
                cursor = transaction.connection.cursor()

                query = table.select(
                    table.id,
                    where=(Coalesce(table.next_call, now) <= now)
                    & ~table.id.in_(skip_task_ids)
                    & (table.active == Literal(True)),
                    order_by=[table.id.asc],
                    limit=1)
                if database.has_select_for():
                    For = database.get_select_for_skip_locked()
                    query.for_ = For('UPDATE')
                cursor.execute(*query)
                row = cursor.fetchone()
                if not row:
                    break
                task_id, = row
                if current_task_id is not None and current_task_id != task_id:
                    # Get another task so reset the transaction setup
                    count = 0
                    current_task_id = None
                    transaction_extras.clear()
                    continue
                task = cls(task_id)

                def duration():
                    return (time.monotonic() - started) * 1000
                started = time.monotonic()
                name = '<Cron %s@%s %s>' % (task.id, db_name, task.method)
                try:
                    if not database.has_select_for():
                        task.lock()
                    with processing(name):
                        task.run_once()
                    task.next_call = task.compute_next_call(now)
                    task.save()
                    logger.info("%s in %i ms", name, duration())
                except Exception as e:
                    transaction.rollback()
                    if isinstance(e, TransactionError):
                        e.fix(transaction_extras)
                        current_task_id = task_id
                        continue
                    if (isinstance(e, backend.DatabaseOperationalError)
                            and count < retry):
                        count += 1
                        current_task_id = task_id
                        logger.debug("Retry: %i", count)
                        continue
                    if isinstance(e, (UserError, UserWarning)):
                        Error.report(task, e)
                        logger.info(
                            "%s failed after %i ms", name, duration())
                    else:
                        logger.exception(
                            "%s failed after %i ms", name, duration())
                    skip_task_ids.append(task_id)
                current_task_id = None
                count = 0
                transaction_extras.clear()
            while transaction.tasks:
                task_id = transaction.tasks.pop()
                run_task(db_name, task_id)
        for task_id in skip_task_ids:
            if task_id < 0:
                continue
            with Transaction().start(db_name, 0) as transaction:
                try:
                    task = cls(task_id)
                    task.next_call = task.compute_next_call(now)
                    task.save()
                except backend.DatabaseOperationalError:
                    transaction.rollback()
        logger.info('cron finished for "%s"', db_name)
