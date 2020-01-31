# Copyright (C) 2020 Cartesi Pte. Ltd.

# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.

# This program is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

# Note: This component currently has dependencies that are licensed under the GNU
# GPL, version 3, and so you should treat this component as a whole as being under
# the GPL version 3. But all Cartesi-written code in this component is licensed
# under the Apache License, version 2, or a compatible permissive license, and can
# be used independently under the Apache v2 license. After this component is
# rewritten, the entire component will be released under the Apache v2 license.

import sys
import os
import sha3
import math
from cobra_hdwallet import HDWallet

DEFAULT_DATA_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONTRACT_PATH = '/opt/cartesi/share/blockchain/contracts/Logger.json'

hdWallet = HDWallet()

class Logger:

    def __init__(self, w3, logger_address, logger_abi):
        self.__w3 = w3

        if "MNEMONIC" in os.environ:
            wallet = hdWallet.create_hdwallet(os.environ.get("MNEMONIC"),
                '',
                int(os.getenv("ACCOUNT_INDEX", default="0")))
            self.__key = bytes.fromhex(wallet['private_key'])
            self.__user = w3.toChecksumAddress(wallet['address'])
        else:
            self.__key = bytes.fromhex(os.environ.get("CARTESI_CONCERN_KEY"))
            self.__user = w3.toChecksumAddress(os.environ.get("CARTESI_CONCERN_ADDRESS"))

        self.__logger = self.__w3.eth.contract(address=logger_address, abi=logger_abi)
        self.__bytes_of_word = 8
        self.__debug = False

    def instantiate(self, page_log2_size, tree_log2_size):
        self.__page_log_2_size = page_log2_size - 3
        self.__tree_log_2_size = tree_log2_size - 3
        self.__page_size = 2**self.__page_log_2_size
        self.__tree_size = 2**self.__tree_log_2_size
        self.__download_cache = {}
        # submission cache from on-chain logger
        # map {hash} value to {index} in logger history
        self.__submission_root_cache = {}
        # local cache
        # map {bytes} blob to {index}
        self.__submission_blob_cache = {}
        # map {indices} tuple to {index}
        self.__submission_index_cache = {}

        if (not self.__w3.isConnected()):
            print("Couldn't connect to node, exiting")
            sys.exit(1)

    def __bytes_from_file(self, filename):
        with open(filename, "rb") as f:
            while True:
                chunk = f.read(self.__bytes_of_word)
                if chunk:
                    yield chunk
                else:
                    break

    def __recover_data_from_root(self, root):

        try:
            cached_data = self.__download_cache.get(root)
            if cached_data is not None:
                return (True, cached_data)

            merkle_filter = self.__logger.events.MerkleRootCalculatedFromData.createFilter(fromBlock=0, argument_filters={'_root': root})

            if(not len(merkle_filter.get_all_entries()) == 0):
                return (True, merkle_filter.get_all_entries()[0]['args']['_data'])

            data = []
            merkle_filter = self.__logger.events.MerkleRootCalculatedFromHistory.createFilter(fromBlock=0, argument_filters={'_root': root})

            if(not len(merkle_filter.get_all_entries()) == 0):
                for index in merkle_filter.get_all_entries()[0]['args']['_indices']:

                    retrieve_filter = self.__logger.events.MerkleRootCalculatedFromData.createFilter(fromBlock=0, argument_filters={'_index': index})
                    if(len(retrieve_filter.get_all_entries()) == 0):
                        retrieve_filter = self.__logger.events.MerkleRootCalculatedFromHistory.createFilter(fromBlock=0, argument_filters={'_index': index})
                    root_at_index = retrieve_filter.get_all_entries()[0]['args']['_root']

                    (ret_at_index, data_at_index) = self.__recover_data_from_root(root_at_index)
                    data += data_at_index

                self.__download_cache[root] = data
                return (True, data)
            return (False, [])

        except ValueError as e:
            print(str(e))

    def __calculate_root_from_hashes(self, hashes):
        
        while len(hashes) > 1:
            results = []
            for x in range(int(len(hashes) / 2)):
                left = hashes.pop(0)
                right = hashes.pop(0)

                k = sha3.keccak_256()
                k.update(left + right)
                results.append(bytes.fromhex(k.hexdigest()))
            hashes = results

        return hashes[0]

    def __get_index_from_root(self, root, log2_size):

        # check if submission exists in the history
        if self.__logger.functions.isLogAvailable(root, int(log2_size)).call():
            index = self.__logger.functions.getLogIndex(root).call()
            if(self.__debug):
                print("find root %s in history %d" % (root.hex(), index))
            return (True, index)
        
        return (False, 0)

    def __send_txn_to_logger(self, txn, isData):
        signed_txn = self.__w3.eth.account.sign_transaction(txn, private_key=self.__key)
        tx_hash = self.__w3.eth.sendRawTransaction(signed_txn.rawTransaction)
        tx_receipt = self.__w3.eth.waitForTransactionReceipt(tx_hash)
        if tx_receipt['status'] == 0:
            raise ValueError(tx_receipt['transactionHash'].hex())
        merkle_filter = None
        if isData:
            merkle_filter = self.__logger.events.MerkleRootCalculatedFromData.createFilter(fromBlock=tx_receipt['blockNumber'])
        else:
            merkle_filter = self.__logger.events.MerkleRootCalculatedFromHistory.createFilter(fromBlock=tx_receipt['blockNumber'])
        merkle_root = merkle_filter.get_all_entries()[0]['args']['_root']
        merkle_log2 = merkle_filter.get_all_entries()[0]['args']['_log2Size']
        merkle_index = merkle_filter.get_all_entries()[0]['args']['_index']

        if(self.__debug):
            print("root is: " + merkle_root.hex())
            print("log2 is: " + str(merkle_log2))
            print("index in the history is: " + str(merkle_index))

        return (merkle_index, merkle_root)

    def submit_indices_to_logger(self, log2_size, indices):

        try:
            # get hashes from history with index
            hashes = []
            for x in indices:
                hashes.append(self.__logger.functions.getLogRoot(x).call())

            root = self.__calculate_root_from_hashes(hashes)

            (exists, index) = self.__get_index_from_root(root, log2_size + 3 + math.log2(len(indices)))
            if exists:
                return (index, root)

            # submit data if is the first time
            nonce = self.__w3.eth.getTransactionCount(self.__user)
            txn = self.__logger.functions.calculateMerkleRootFromHistory(log2_size, indices).buildTransaction({"nonce": nonce, "from": self.__user})
            return self.__send_txn_to_logger(txn, False)

        except ValueError as e:
            print("calculateMerkleRoot REVERT transaction: " + str(e))

    def submit_data_to_logger(self, data):

        try:
            # calculate hash locally
            hashes = []
            padded_data = list(data)
            for x in range(self.__page_size - len(data)):
                padded_data.append(bytes(self.__bytes_of_word))
            for x in range(self.__page_size):
                k = sha3.keccak_256()
                k.update(padded_data[x])
                hashes.append(bytes.fromhex(k.hexdigest()))

            root = self.__calculate_root_from_hashes(hashes)

            (exists, index) = self.__get_index_from_root(root, self.__page_log_2_size + 3)
            if exists:
                return (index, root)

            # submit data if is the first time
            nonce = self.__w3.eth.getTransactionCount(self.__user)
            txn = self.__logger.functions.calculateMerkleRootFromData(self.__page_log_2_size, data).buildTransaction({"nonce": nonce, "from": self.__user})
            return self.__send_txn_to_logger(txn, True)

        except ValueError as e:
            print("calculateMerkleRoot REVERT transaction: " + str(e))

    def submit_file(self, filename):

        data = []
        indices = []
        root = bytes(32)
        count = 2**(self.__tree_log_2_size - self.__page_log_2_size)
        for b in self.__bytes_from_file(filename):
            data.append(b)
            if(len(data) == self.__page_size):
                cached_index = self.__submission_blob_cache.get(tuple(data))
                if cached_index is not None:
                    indices.append(cached_index)
                else:
                    (index, root) = self.submit_data_to_logger(data)
                    indices.append(index)
                    self.__submission_blob_cache[tuple(data)] = index
                data = []
                count -= 1

        if(len(data) != 0):
            (index, root) = self.submit_data_to_logger(data)
            indices.append(index)
            count -= 1

        data = []
        data.append(bytes(self.__bytes_of_word))

        if(count > 0):
            (index, root) = self.submit_data_to_logger(data)
            while(count > 0):
                indices.append(index)
                count -= 1

        index_log2_size = self.__page_log_2_size
        while(len(indices) > 1):
            new_indices = []
            for x in range(int(len(indices) / 2)):
                partial_indices = []
                partial_indices.append(indices.pop(0))
                partial_indices.append(indices.pop(0))
                cached_index = self.__submission_index_cache.get(tuple(partial_indices))
                if cached_index is not None:
                    new_indices.append(cached_index)
                else:
                    (index, root) = self.submit_indices_to_logger(index_log2_size, partial_indices)
                    new_indices.append(index)
                    self.__submission_index_cache[tuple(partial_indices)] = index
            indices = new_indices
            index_log2_size += 1

        return root

    def download_file(self, root, filename):

        (succ, data) = self.__recover_data_from_root(root)

        bytes_count = 0
        for b in data:
            bytes_count += len(b)

        if 2**(self.__tree_log_2_size + 3) != bytes_count:
            raise ValueError("Downloaded file({} bytes) doesn't match log2 size({})".format(bytes_count, self.__tree_log_2_size + 3))

        if(succ):
            if(self.__debug):
                print("data is: " + str(data))
            with open(filename, "wb") as f:
                for b in data:
                    f.write(b)
        else:
            print("The Merkle root is not found in the Logger history")
