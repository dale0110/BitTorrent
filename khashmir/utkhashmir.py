import khashmir, knode
from actions import *
from khash import newID
from krpc import KRPCProtocolError, KRPCFailSilently
from cache import Cache
from defer import Deferred
from sha import sha
from util import *
from threading import Thread
from socket import gethostbyname

TOKEN_UPDATE_INTERVAL = 5 * 60 # five minutes
NUM_PEERS = 50 # number of peers to return

class UTNode(knode.KNodeBase):
    def announcePeer(self, info_hash, port, khashmir_id):
        assert type(port) == type(1)
        assert type(info_hash) == type('')
        assert type(khashmir_id) == type('')
        assert len(info_hash) == 20
        assert len(khashmir_id) == 20

        try:
            token = self.table.tcache[self.id]
        except:
            token = None
        if token:
            df = self.conn.sendRequest('announce_peer', {'info_hash':info_hash,
                                                         'port':port,
                                                         'id':khashmir_id,
                                                         'token':token})
        else:
            df = Deferred()
            df.errback("no write token for node")
        #df.addErrback(self.errBack)
        df.addCallback(self.checkSender)
        return df
    
    def getPeers(self, info_hash, khashmir_id):
        df = self.conn.sendRequest('get_peers', {'info_hash':info_hash, 'id':khashmir_id})
        df.addErrback(self.errBack)
        df.addCallback(self.checkSender)
        return df

    def checkSender(self, dict):
        d = knode.KNodeBase.checkSender(self, dict)
        try:
            self.table.tcache[self.id] = d['rsp']['token']
        except KeyError:
            pass
        return d
    
class UTStoreValue(StoreValue):
    def callNode(self, node, f):
        return f(self.target, self.value, node.token, self.table.node.id)
    
class UTKhashmir(khashmir.KhashmirBase):
    _Node = UTNode

    def setup(self, host, port, data_dir):
        khashmir.KhashmirBase.setup(self, host, port,data_dir)
        self.cur_token = self.last_token = sha('')
        self.tcache = Cache()
        self.gen_token(loop=True)
        self.expire_cached_tokens(loop=True)
        
    def expire_cached_tokens(self, loop=False):
        self.tcache.expire(time() - TOKEN_UPDATE_INTERVAL)
        if loop:
            self.rawserver.add_task(self.expire_cached_tokens, TOKEN_UPDATE_INTERVAL, (True,))
                                
    def gen_token(self, loop=False):
        self.last_token = self.cur_token
        self.cur_token = sha(newID())
        if loop:
            self.rawserver.add_task(self.gen_token, TOKEN_UPDATE_INTERVAL, (True,))

    def get_token(self, host, port):
        x = self.cur_token.copy()
        x.update("%s%s" % (host, port))
        h = x.digest()
        return h

        
    def val_token(self, token, host, port):
        x = self.cur_token.copy()
        x.update("%s%s" % (host, port))
        a = x.digest()
        if token == a:
            return True

        x = self.last_token.copy()
        x.update("%s%s" % (host, port))
        b = x.digest()
        if token == b:
            return True

        return False

    def addContact(self, host, port, callback=None):
        # use dns on host, then call khashmir.addContact
        Thread(target=self._get_host, args=[host, port, callback]).start()

    def _get_host(self, host, port, callback):
        ip = gethostbyname(host)
        self.rawserver.external_add_task(self._got_host, 0, (host, port, callback))

    def _got_host(self, host, port, callback):
        khashmir.KhashmirBase.addContact(self, host, port, callback)
        
    def krpc_find_node(self, target, id, _krpc_sender):
        d = khashmir.KhashmirBase.krpc_find_node(self, target, id, _krpc_sender)
        d['token'] = self.get_token(_krpc_sender[0], _krpc_sender[1])
        return d
            

    def announcePeer(self, info_hash, port, callback=None):
        """ stores the value for key in the global table, returns immediately, no status 
            in this implementation, peers respond but don't indicate status to storing values
            a key can have many values
        """
        def _storeValueForKey(nodes, key=info_hash, value=port, response=callback , table=self.table):
            if not response:
                # default callback
                def _storedValueHandler(sender):
                    pass
                response=_storedValueHandler
            action = UTStoreValue(self, key, value, response, self.rawserver.add_task, "announcePeer")
            self.rawserver.add_task(action.goWithNodes, 0, (nodes,))
            
        # this call is asynch
        self.findNode(info_hash, _storeValueForKey)
                    
    def krpc_announce_peer(self, info_hash, port, id, token, _krpc_sender):
        sender = {'id' : id}
        sender['host'] = _krpc_sender[0]
        sender['port'] = _krpc_sender[1]
        if not self.val_token(token, sender['host'], sender['port']):
            raise KRPCProtocolError("Invalid Write Token")
        value = compact_peer_info(_krpc_sender[0], port)
        self.store[info_hash] = value
        n = self.Node().initWithDict(sender)
        n.conn = self.udp.connectionForAddr((n.host, n.port))
        self.insertNode(n, contacted=0)
        return {"id" : self.node.id}

    def retrieveValues(self, key):
        try:
            l = self.store.sample(key, NUM_PEERS)
        except KeyError:
            l = []
        return l

    def getPeers(self, info_hash, callback, searchlocal = 1):
        """ returns the values found for key in global table
            callback will be called with a list of values for each peer that returns unique values
            final callback will be an empty list - probably should change to 'more coming' arg
        """
        nodes = self.table.findNodes(info_hash)
        
        # get locals
        if searchlocal:
            l = self.retrieveValues(info_hash)
            if len(l) > 0:
                self.rawserver.add_task(callback, 0, ([reducePeers(l)],))
        else:
            l = []
        # create our search state
        state = GetValue(self, info_hash, callback, self.rawserver.add_task, 'getPeers')
        self.rawserver.add_task(state.goWithNodes, 0, (nodes, l))

    def krpc_get_peers(self, info_hash, id, _krpc_sender):
        sender = {'id' : id}
        sender['host'] = _krpc_sender[0]
        sender['port'] = _krpc_sender[1]        
        n = self.Node().initWithDict(sender)
        n.conn = self.udp.connectionForAddr((n.host, n.port))
        self.insertNode(n, contacted=0)
    
        l = self.retrieveValues(info_hash)
        if len(l) > 0:
            return {'values' : [reducePeers(l)],
                    "id": self.node.id,
                    "token" : self.get_token(sender['host'], sender['port'])}
        else:
            nodes = self.table.findNodes(info_hash, invalid=False)
            nodes = [node.senderDict() for node in nodes]
            return {'nodes' : packNodes(nodes),
                    "id": self.node.id,
                    "token" : self.get_token(sender['host'], sender['port'])}

