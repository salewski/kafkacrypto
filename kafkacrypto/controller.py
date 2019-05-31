from threading import Thread
import inspect
import pysodium
from time import time
import msgpack
from kafka import TopicPartition
from kafkacrypto.base import KafkaCryptoBase
from kafkacrypto.exceptions import KafkaCryptoControllerError
from kafkacrypto.provisioners import Provisioners

class KafkaCryptoController(KafkaCryptoBase):
  """ A simple controller implementation, resigning requests for keys
      for a particular resource, using our key (signed by ROT), if
      request is signed by a known provisioner. To function properly,
      and not miss messages, the provided KafkaConsumer must be 
      configured so that client_id and group_id are both set to nodeID,
      and that enable_auto_commit (automatic committing) is False.

  Keyword Arguments:
              nodeID (str): Node ID
        kp (KafkaProducer): Pre-initialized KafkaProducer, ready for
                            handling crypto-keying messages. Should
                            not be used elsewhere, as this class
                            changes some configuration values.
        kc (KafkaConsumer): Pre-initialized KafkaConsumer, ready for      
       	       	       	    handling crypto-keying messages. Should
                            not be used elsewhere, as this class
                            changes some configuration values.
       cryptokey (str,obj): Either a filename in which the crypto
                            private key is stored, or an object 
                            implementing the necessary functions
                            (encrypt_key, decrypt_key, sign_spk)
    provisioners (str,obj): Either a filename in which the allowed
                            provisioners are stored, or an object
                            implementing the necessary functions
                            (reencrypt_request)
  """

  def __init__(self, nodeID, kp, kc, config=None, cryptokey=None, provisioners=None):
    super().__init__(nodeID, kp, kc, config, cryptokey)
    if (self._kc.config['enable_auto_commit'] != False):
      print("Warning: Auto commit not disabled, controller may miss messages.")
    if (self._kc.config['group_id'] is None):
      print("Warning: Group ID not set, controller may miss messages.")
    if (provisioners is None):
      provisioners = nodeID + ".provisioners"
    if (isinstance(provisioners,(str,))):
      provisioners = Provisioners(file=provisioners)
    if (not hasattr(provisioners, 'reencrypt_request') or not inspect.isroutine(provisioners.reencrypt_request)):
      raise KafkaCryptoControllerError("Invalid provisioners source supplied!")

    self._provisioners = provisioners
    self._last_subscribed_time = 0
    self._mgmt_thread = Thread(target=self._process_mgmt_messages,daemon=True)
    self._mgmt_thread.start()

  # Main background processing loop. Must assume that it can "die" at any
  # time, even mid-stride, so ordering of operations to ensure atomicity and
  # durability is critical.
  def _process_mgmt_messages(self):
    while True:
      # First, (Re)subscribe if needed
      if ((time()-self._last_subscribed_time) >= self.MGMT_SUBSCRIBE_INTERVAL):
        trx = "(.*\\" + self.TOPIC_SUFFIX_SUBS.decode('utf-8') + "$)"
        self._kc.subscribe(pattern=trx)
        self._last_subscribed_time = time()

      # Second, process messages
      # we are the only thread ever using _kc, _kp, so we do not need the lock to use them
      msgs = self._kc.poll(timeout_ms=self.MGMT_POLL_INTERVAL, max_records=self.MGMT_POLL_RECORDS)
      # but to actually process messages, we need the lock
      for tp,msgset in msgs.items():
        self._lock.acquire()
        for msg in msgset:
          topic = msg.topic
          if (isinstance(topic,(str,))):
            topic = topic.encode('utf-8')
          if topic[-len(self.TOPIC_SUFFIX_SUBS):] == self.TOPIC_SUFFIX_SUBS:
            root = topic[:-len(self.TOPIC_SUFFIX_SUBS)]
            # New consumer encryption key. Validate
            k,v = self._provisioners.reencrypt_request(root, cryptokey=self._cryptokey, msgkey=msg.key, msgval=msg.value)
            # Valid request, resign and retransmit
            if (not (k is None)) or (not (v is None)):
              self._kp.send((root + self.TOPIC_SUFFIX_REQS).decode('utf-8'), key=k, value=v)
          else:
            # unknown object
            print("Unknown topic type in message: ", msg)
        self._lock.release()

      # Third, commit offsets
      if (self._kc.config['group_id'] is not None):
        self._kc.commit()
  
      # Finally, loop back to poll again
  # end of __process_mgmt_messages
