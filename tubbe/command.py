# coding=utf8



import abc
import gevent
import logging
import signal
import datetime
import traceback

from functools import wraps
from collections import OrderedDict
from contextlib import contextmanager

from . import circuit_breaker
from . import exceptions

_logger = logging.getLogger(__name__)

__all__ = ['BaseAsyncCommand', 'BaseSyncCommand']


def _fallback(callback):
    def deco(f):
        @wraps(f)
        def _(*a, **kw):
            command = a[0]
            name = command.name
            timeout = command.timeout
            start_time = datetime.datetime.now()
            action = f.__name__
            fallback = callback.__name__
            try:
                v = f(*a, **kw)
                _info = OrderedDict([
                    ('start_time', start_time),
                    ('command', name),
                    ('action', action),
                    ('fallback', fallback),
                    ('timeout', timeout),
                    ('success', True),
                    ('end_time', datetime.datetime.now()),
                    ])
                command.logger.info('\t'.join(['%s=%s' % t for t in _info.items()]))
                return v
            except:
                _info = OrderedDict([
                    ('start_time', start_time),
                    ('command', name),
                    ('action', action),
                    ('fallback', fallback),
                    ('timeout', timeout),
                    ('success', False),
                    ('end_time', datetime.datetime.now()),
                    ])
                command.logger.error('\t'.join(['%s=%s' % t for t in _info.items()]))
                return callback(*a, **kw)
        return _
    return deco


@contextmanager
def _timeout(seconds):
    def _handle_timeout(signum, frame):
        raise exceptions.TubbeTimeoutException('timeout: {}'.format(seconds))

    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.alarm(seconds)
    yield


def _get_fullname(f):
    return f.__module__ + "." + f.__name__

class AbstractCommand(object):

    __metaclass__ = abc.ABCMeta

    def __init__(self, name, timeout=None, logger=None):
        self.name = name
        self.timeout = timeout
        self.logger = logger or _logger

    @abc.abstractmethod
    def run(self, *a, **kw):
        pass

    @abc.abstractmethod
    def fallback(self, *a, **kw):
        pass

    @abc.abstractmethod
    def cache(self, *a, **kw):
        pass

    @abc.abstractmethod
    def validate(self, result):
        pass


class BaseCommand(AbstractCommand):

    def run(self, *a, **kw):
        raise NotImplemented

    def fallback(self, *a, **kw):
        raise NotImplemented

    def cache(self, *a, **kw):
        raise NotImplemented

    def validate(self, result):
        raise NotImplemented


class BaseAsyncCommand(BaseCommand):

    def _do_cache(self, *a, **kw):
        start_time = datetime.datetime.now()
        return self.cache(*a, **kw)

    @_fallback(_do_cache)
    def _do_fallback(self, *a, **kw):
        start_time = datetime.datetime.now()
        job = gevent.Greenlet.spawn(self.fallback, *a, **kw)
        job.join(self.timeout)
        v = job.get(block=False, timeout=self.timeout)
        if not self.validate(v):
            raise exceptions.TubbeValidationException
        return v

    @_fallback(_do_fallback)
    def execute(self, *a, **kw):
        start_time = datetime.datetime.now()
        if circuit_breaker.is_break(self):
            raise exceptions.TubbeCircuitBrokenException
        job = gevent.Greenlet.spawn(self.run, *a, **kw)
        job.join(self.timeout)
        v = job.get(block=False, timeout=self.timeout)
        if not self.validate(v):
            raise exceptions.TubbeValidationException
        return v


class BaseSyncCommand(BaseCommand):

    def _do_cache(self, *a, **kw):
        start_time = datetime.datetime.now()
        return self.cache(*a, **kw)

    @_fallback(_do_cache)
    def _do_fallback(self, *a, **kw):
        with _timeout(self.timeout):
            start_time = datetime.datetime.now()
            v = self.fallback(*a, **kw)
            if not self.validate(v):
                raise exceptions.TubbeValidationException
            return v

    @_fallback(_do_fallback)
    def execute(self, *a, **kw):
        with _timeout(self.timeout):
            start_time = datetime.datetime.now()
            if circuit_breaker.is_break(self):
                raise exceptions.TubbeCircuitBrokenException

            v = self.run(*a, **kw)
            if not self.validate(v):
                raise exceptions.TubbeValidationException
            return v

