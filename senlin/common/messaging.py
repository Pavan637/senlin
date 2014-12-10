# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import eventlet
from oslo.config import cfg
import oslo.messaging
from oslo.serialization import jsonutils
from osprofiler import profiler

from senlin.common import context


TRANSPORT = None
NOTIFIER = None

_ALIASES = {
    'senlin.openstack.common.rpc.impl_kombu': 'rabbit',
    'senlin.openstack.common.rpc.impl_qpid': 'qpid',
    'senlin.openstack.common.rpc.impl_zmq': 'zmq',
}


class RequestContextSerializer(oslo.messaging.Serializer):
    def __init__(self, base):
        self._base = base

    def serialize_entity(self, ctxt, entity):
        if not self._base:
            return entity
        return self._base.serialize_entity(ctxt, entity)

    def deserialize_entity(self, ctxt, entity):
        if not self._base:
            return entity
        return self._base.deserialize_entity(ctxt, entity)

    @staticmethod
    def serialize_context(ctxt):
        _context = ctxt.to_dict()
        prof = profiler.get()
        if prof:
            trace_info = {
                "hmac_key": prof.hmac_key,
                "base_id": prof.get_base_id(),
                "parent_id": prof.get_id()
            }
            _context.update({"trace_info": trace_info})
        return _context

    @staticmethod
    def deserialize_context(ctxt):
        trace_info = ctxt.pop("trace_info", None)
        if trace_info:
            profiler.init(**trace_info)
        return context.RequestContext.from_dict(ctxt)


class JsonPayloadSerializer(oslo.messaging.NoOpSerializer):
    @classmethod
    def serialize_entity(cls, context, entity):
        return jsonutils.to_primitive(entity, convert_instances=True)


def setup(url=None, optional=False):
    """Initialise the oslo.messaging layer."""
    global TRANSPORT, NOTIFIER

    if url and url.startswith("fake://"):
        # NOTE(sileht): oslo.messaging fake driver uses time.sleep
        # for task switch, so we need to monkey_patch it
        eventlet.monkey_patch(time=True)

    if not TRANSPORT:
        oslo.messaging.set_transport_defaults('senlin')
        exmods = ['senlin.common.exception']
        try:
            TRANSPORT = oslo.messaging.get_transport(
                cfg.CONF, url, allowed_remote_exmods=exmods, aliases=_ALIASES)
        except oslo.messaging.InvalidTransportURL as e:
            TRANSPORT = None
            if not optional or e.url:
                # NOTE(sileht): oslo.messaging is configured but unloadable
                # so reraise the exception
                raise

    if not NOTIFIER and TRANSPORT:
        serializer = RequestContextSerializer(JsonPayloadSerializer())
        NOTIFIER = oslo.messaging.Notifier(TRANSPORT, serializer=serializer)


def cleanup():
    """Cleanup the oslo.messaging layer."""
    global TRANSPORT, NOTIFIER
    if TRANSPORT:
        TRANSPORT.cleanup()
        TRANSPORT = NOTIFIER = None


def get_rpc_server(target, endpoint):
    """Return a configured oslo.messaging rpc server."""
    serializer = RequestContextSerializer(JsonPayloadSerializer())
    return oslo.messaging.get_rpc_server(TRANSPORT, target, [endpoint],
                                         executor='eventlet',
                                         serializer=serializer)


def get_rpc_client(**kwargs):
    """Return a configured oslo.messaging RPCClient."""
    target = oslo.messaging.Target(**kwargs)
    serializer = RequestContextSerializer(JsonPayloadSerializer())
    return oslo.messaging.RPCClient(TRANSPORT, target,
                                    serializer=serializer)


def get_notifier(publisher_id):
    """Return a configured oslo.messaging notifier."""
    return NOTIFIER.prepare(publisher_id=publisher_id)
