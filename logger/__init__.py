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


class Logger:

    def __init__(self, w3, logger_address, logger_abi):
        self.__w3 = w3

        if "MNEMONIC" in os.environ:
            # recreate wallet from MNEMONIC env variable
            hdWallet = HDWallet()
            wallet = hdWallet.create_hdwallet(os.environ.get("MNEMONIC"),
                '',
                int(os.getenv("ACCOUNT_INDEX", default="0")))
            self.__key = bytes.fromhex(wallet['private_key'])
            self.__user = w3.toChecksumAddress(wallet['address'])
        elif ("CARTESI_CONCERN_KEY" in os.environ and "CARTESI_CONCERN_ADDRESS" in os.environ):
            # read key and address from env variable
            self.__key = bytes.fromhex(os.environ.get("CARTESI_CONCERN_KEY"))
            self.__user = w3.toChecksumAddress(os.environ.get("CARTESI_CONCERN_ADDRESS"))
        else:
            # read address from eth server, key will not be used
            self.__key = None
            self.__user = self.__w3.eth.accounts[0]

        self.__logger = self.__w3.eth.contract(address=logger_address, abi=logger_abi)
        self.__log2_bytes_of_word = 3
        self.__bytes_of_word = 8
        self.__debug = False
        self.__MerkleRootCalculatedFromDataHash = self.__w3.keccak(text="MerkleRootCalculatedFromData(uint256,bytes8[],bytes32,uint64)")
        self.__MerkleRootCalculatedFromHistoryHash = self.__w3.keccak(text="MerkleRootCalculatedFromHistory(uint256,uint256[],bytes32,uint64)")

    def instantiate(self, page_log2_size, tree_log2_size):
        self.__page_log_2_size = page_log2_size
        self.__tree_log_2_size = tree_log2_size
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
        self.__download_progress = 0
        self.__submission_progress = 0

        self.__total_pages = 2**(self.__tree_log_2_size - self.__page_log_2_size)
        self.__total_levels = (self.__tree_log_2_size - self.__page_log_2_size)
        self.__progress_per_page = 1/max(1, self.__total_pages) * 100
        self.__total_nodes = self.__total_pages * 2 - 1
        self.__progress_per_node = 1/self.__total_nodes * 100
        self.__total_pairs = self.__total_pages - 1
        self.__progress_per_pair = 1/max(1, self.__total_pairs) * 100
        self.__recover_count = 0

        # event logs cache
        self.__next_block_number = 0
        self.__block_step = 1000000
        self.__logs = []

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

    def __update_download_progress(self):
        self.__recover_count += 1
        self.__download_progress = int(self.__recover_count * self.__progress_per_node)

    def __get_all_contract_logs(self):
        block_number = self.__w3.eth.blockNumber
        from_block = self.__next_block_number

        while from_block <= block_number:
            to_block = min(from_block + self.__block_step - 1, block_number)

            get_logs = self.__w3.eth.getLogs(
            {
                'fromBlock': from_block,
                'toBlock': to_block,
                'address': self.__logger.address})

            self.__logs.extend(get_logs)
            self.__next_block_number = to_block + 1

            from_block += self.__block_step

        return self.__logs

    def __filter_logs_history(self, logs):
        events = []
        for log in logs:
            if log['topics'][0] == self.__MerkleRootCalculatedFromHistoryHash:
                event = self.__logger.events.MerkleRootCalculatedFromHistory().processLog(log)
                events.append(event)

        return events

    def __filter_logs_data(self, logs):
        events = []
        for log in logs:
            if log['topics'][0] == self.__MerkleRootCalculatedFromDataHash:
                event = self.__logger.events.MerkleRootCalculatedFromData().processLog(log)
                events.append(event)

        return events

    def __search_logs_root(self, root):
        for log in self.__logs:
            event = None
            if log['topics'][0] == self.__MerkleRootCalculatedFromDataHash:
                event = self.__logger.events.MerkleRootCalculatedFromData().processLog(log)
            elif log['topics'][0] == self.__MerkleRootCalculatedFromHistoryHash:
                event = self.__logger.events.MerkleRootCalculatedFromHistory().processLog(log)
            if event and event['args']['_root'] == root:
                    return event

    def __search_logs_index(self, index):
        for log in self.__logs:
            event = None
            if log['topics'][0] == self.__MerkleRootCalculatedFromDataHash:
                event = self.__logger.events.MerkleRootCalculatedFromData().processLog(log)
            elif log['topics'][0] == self.__MerkleRootCalculatedFromHistoryHash:
                event = self.__logger.events.MerkleRootCalculatedFromHistory().processLog(log)
            if event and event['args']['_index'] == index:
                return event

    def __get_index_from_root(self, root, log2_size):
        # check if submission exists in the history
        if self.__logger.functions.isLogAvailable(root, int(log2_size)).call():
            index = self.__logger.functions.getLogIndex(root).call()
            if(self.__debug):
                print("find root %s in history %d" % (root.hex(), index))
            return (True, index)

        return (False, 0)

    def __send_txn_to_logger(self, txn, isData):
        if self.__key is None:
            # let eth server sign transaction
            tx_hash = self.__w3.eth.sendTransaction(txn)
        else:
            # sign transaction using key, and send raw
            signed_txn = self.__w3.eth.account.sign_transaction(txn, private_key=self.__key)
            tx_hash = self.__w3.eth.sendRawTransaction(signed_txn.rawTransaction)

        tx_receipt = self.__w3.eth.waitForTransactionReceipt(tx_hash)
        if tx_receipt['status'] == 0:
            raise ValueError(tx_receipt['transactionHash'].hex())

        events = []
        if isData:
            events = self.__filter_logs_data(tx_receipt['logs'])
        else:
            events = self.__filter_logs_history(tx_receipt['logs'])

        if events:
            event = events[0]['args']
            merkle_root = event['_root']
            merkle_log2 = event['_log2Size']
            merkle_index = event['_index']

            if(self.__debug):
                print("root is: " + merkle_root.hex())
                print("log2 is: " + str(merkle_log2))
                print("index in the history is: " + str(merkle_index))

            return (merkle_index, merkle_root)

    def get_download_progress(self):
        return self.__download_progress

    def get_submission_progress(self):
        return self.__submission_progress

    def submit_indices_to_logger(self, log2_size, indices):
        try:
            # get hashes from history with index
            hashes = []
            for x in indices:
                hashes.append(self.__logger.functions.getLogRoot(x).call())

            root = calculate_root_from_hashes(hashes)

            (exists, index) = self.__get_index_from_root(root, log2_size + math.log2(len(indices)))
            if exists:
                return (index, root)

            # submit data if is the first time
            nonce = self.__w3.eth.getTransactionCount(self.__user, "pending")
            txn = self.__logger.functions.calculateMerkleRootFromHistory(log2_size, indices).buildTransaction({"nonce": nonce, "from": self.__user})
            return self.__send_txn_to_logger(txn, False)

        except ValueError as e:
            print("calculateMerkleRoot REVERT transaction: " + str(e))

    def recover_data_from_root(self, root):
        try:
            cached_data = self.__download_cache.get(root)
            if cached_data is not None:
                self.__update_download_progress()
                return (True, cached_data)

            logs = self.__get_all_contract_logs()
            event = self.__search_logs_root(root)

            if event:
                if event['event'] == 'MerkleRootCalculatedFromData':
                    args = event['args']
                    data = args['_data']
                    log2_size = args['_log2Size']
                    expected_size = 2 ** (log2_size - self.__log2_bytes_of_word)

                    for x in range(expected_size - len(data)):
                        # padding zero to expected log2 size
                        data.append(bytes(self.__bytes_of_word))

                    self.__update_download_progress()
                    return (True, data)
                else:
                    # MerkleRootCalculatedFromHistory
                    data = []
                    args = event['args']

                    for index in args['_indices']:
                        index_event = self.__search_logs_index(index)

                        if index_event:
                            index_args = index_event['args']
                            root_at_index = index_args['_root']

                            (_, data_at_index) = self.recover_data_from_root(root_at_index)
                            data += data_at_index

                    self.__download_cache[root] = data
                    self.__update_download_progress()
                    return (True, data)

            return (False, [])

        except ValueError as e:
            print(str(e))

    def submit_data_to_logger(self, data):
        try:
            # calculate hash locally
            hashes = []
            padded_data = list(data)
            for x in range(int(self.__page_size/self.__bytes_of_word) - len(data)):
                # padding zero to page size
                padded_data.append(bytes(self.__bytes_of_word))
            for x in range(int(self.__page_size/self.__bytes_of_word)):
                k = sha3.keccak_256()
                # padding zero to word size
                padded_data[x] += b'0' * (self.__bytes_of_word - len(padded_data[x]))
                k.update(padded_data[x])
                hashes.append(bytes.fromhex(k.hexdigest()))

            root = calculate_root_from_hashes(hashes)

            (exists, index) = self.__get_index_from_root(root, self.__page_log_2_size)
            if exists:
                return (index, root)

            # submit data if is the first time
            nonce = self.__w3.eth.getTransactionCount(self.__user, "pending")
            txn = self.__logger.functions.calculateMerkleRootFromData(self.__page_log_2_size, data).buildTransaction({"nonce": nonce, "from": self.__user})
            return self.__send_txn_to_logger(txn, True)

        except ValueError as e:
            print("calculateMerkleRoot REVERT transaction: " + str(e))

    def submit_file(self, filename):
        data = []
        indices = []
        root = bytes(32)
        count = self.__total_pages
        for b in self.__bytes_from_file(filename):
            data.append(b)
            if((len(data) * self.__bytes_of_word) == self.__page_size):
                cached_index = self.__submission_blob_cache.get(tuple(data))
                if cached_index is not None:
                    indices.append(cached_index)
                else:
                    (index, root) = self.submit_data_to_logger(data)
                    indices.append(index)
                    self.__submission_blob_cache[tuple(data)] = index
                data = []
                count -= 1

                self.__submission_progress = int(self.__progress_per_page/2 * (self.__total_pages - count))

        if(len(data) != 0):
            (index, root) = self.submit_data_to_logger(data)
            indices.append(index)
            count -= 1
            self.__submission_progress = int(self.__progress_per_page/2 * (self.__total_pages - count))

        data = []
        data.append(bytes(self.__bytes_of_word))

        if(count > 0):
            (index, root) = self.submit_data_to_logger(data)
            while(count > 0):
                indices.append(index)
                count -= 1
                self.__submission_progress = int(self.__progress_per_page/2 * (self.__total_pages - count))

        self.__submission_progress = 50

        index_log2_size = self.__page_log_2_size
        count = 0
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

                count += 1
                self.__submission_progress = 50 + int(count * self.__progress_per_pair/2)
            indices = new_indices
            index_log2_size += 1

        return root

    def download_file(self, root, filename):
        (succ, data) = self.recover_data_from_root(root)

        bytes_count = 0
        for b in data:
            bytes_count += len(b)

        expected_size = 2**(self.__tree_log_2_size)

        if(succ):
            if(self.__debug):
                print("data is: " + str(data))
            with open(filename, "wb") as f:
                for b in data:
                    f.write(b)

                if expected_size != bytes_count:
                    f.truncate(expected_size)

        else:
            print("The Merkle root is not found in the Logger history")

        return succ

def calculate_root_from_hashes(hashes):
        while len(hashes) > 1:
            results = []
            for _ in range(int(len(hashes) / 2)):
                left = hashes.pop(0)
                right = hashes.pop(0)

                k = sha3.keccak_256()
                k.update(left + right)
                results.append(bytes.fromhex(k.hexdigest()))
            hashes = results

        return hashes[0]
