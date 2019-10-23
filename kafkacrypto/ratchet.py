from kafkacrypto.keygenerator import KeyGenerator
from kafkacrypto.exceptions import KafkaCryptoRatchetError
import pysodium
import msgpack

class Ratchet(KeyGenerator):
  """Class utilizing file-backed storage to provide a ratcheting key generator.

  Keyword Arguments:
          file (str,file): Filename or File IO object for storing ratchet info.
                           Must be seekable, with read/write permission, and
                           honor sync requests. If it is a File IO object,
       	                   a single write call should be atomic (all or nothing).
  """
  #
  # Ratchet global configuration. These define the parameters
  # to generate the *next* secret key based on the current one.
  #
  __ctx = b'ratchet' + (b'\x00' * 9)

  #
  # Per instance, defined in init
  # __file: File object
  # __keyidx: unsigned integer index of current key index/ID
  #
  def __init__(self, file):
    super().__init__()
    if (isinstance(file, (str))):
      file = open(file, 'rb+', 0)
    self.__file = file
    self.increment()

  def increment(self):
    self.__file.seek(0,0)
    contents = msgpack.unpackb(self.__file.read())
    self.__keyidx = contents[0]
    self.rekey(contents[1])
    self.__file.seek(0,0)
    newkey,nonce = self.generate(ctx=self.__ctx,keysize=self.SECRETSIZE,noncesize=0)
    # In general there is no guarantee that write and then flush will atomically overwrite the
    # previous values. However, in this specific case, this is the best possible approach:
    # the total size of data being written is << 512 bytes (a single sector), meaning that
    # on any block-based device either the new data will be written, or it won't be, with no
    # intermediate possibilities, and thus is in practice atomic. If the provided file was
    # a file I/O object, it should explicitly have atomic writes.
    # We do not use atomic write approaches based on renames due to the possibility of leaving
    # secret key material on disk if temporary files are not appropriately cleaned up.
    self.__file.write(msgpack.packb([self.__keyidx+1,newkey]))
    self.__file.flush()
    self.__file.seek(0,0)

  def get_key_value_generators(self, topic, node=None):
    if (isinstance(topic,(str))):
      topic = bytes(topic, 'utf-8')
    if not (node is None) and isinstance(node,(str)):
      node = bytes(node, 'utf-8')
    # pysodium silently computes the hash of an empty string if input is not bytes, so check for
    # and catch that.
    if (not isinstance(topic, (bytes,bytearray))):
      raise KafkaCryptoRatchetError("Topic is not bytes!")
    hash = pysodium.crypto_hash_sha256(topic)
    # generate per topic key
    key,_ = self.generate(salt=hash[0:self.SALTSIZE],ctx=hash[self.SALTSIZE:],keysize=self.SECRETSIZE,noncesize=0)
    ki = self.__keyidx
    if node is not None:
      ki = ki.to_bytes(16, byteorder='big')
      ki = pysodium.crypto_generichash(node + ki)
    kg, vg = KeyGenerator.get_key_value_generators(key)
    return (ki, key, kg, vg)
