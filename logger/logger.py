# Copyright 2019 Cartesi Pte. Ltd.

# Licensed under the Apache License, Version 2.0 (the "License"); you may not use
# this file except in compliance with the License. You may obtain a copy of the
# License at http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

import sys
from web3 import Web3

class Logger:

    def __init__(self, endpoint, logger_address, logger_abi):
        self.__w3 = Web3(Web3.HTTPProvider(endpoint, request_kwargs={'timeout': 60}))
        self.__logger = self.__w3.eth.contract(address=logger_address, abi=logger_abi)
        self.__bytes_of_word = 8
        self.__debug = False

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
            merkle_filter = self.__logger.events.MerkleRootCalculatedFromData.createFilter(fromBlock=0, argument_filters={'_root':root})

            if(not len(merkle_filter.get_all_entries()) == 0):
                return (True, merkle_filter.get_all_entries()[0]['args']['_data'])
            
            data = []
            merkle_filter = self.__logger.events.MerkleRootCalculatedFromHistory.createFilter(fromBlock=0, argument_filters={'_root':root})

            if(not len(merkle_filter.get_all_entries()) == 0):
                for index in merkle_filter.get_all_entries()[0]['args']['_indices']:

                    retrieve_filter = self.__logger.events.MerkleRootCalculatedFromData.createFilter(fromBlock=0, argument_filters={'_index':index})
                    if(len(retrieve_filter.get_all_entries()) == 0):
                        retrieve_filter = self.__logger.events.MerkleRootCalculatedFromHistory.createFilter(fromBlock=0, argument_filters={'_index':index})
                    root_at_index = retrieve_filter.get_all_entries()[0]['args']['_root']

                    (ret_at_index, data_at_index) = self.__recover_data_from_root(root_at_index)
                    data += data_at_index

                return (True, data)
            return (False, [])

        except ValueError as e:
            print(str(e))

    def instantiate(self, page_log2_size, tree_log2_size):
        self.__page_log_2_size = page_log2_size
        self.__tree_log_2_size = tree_log2_size
        self.__page_size = 2**self.__page_log_2_size
        self.__tree_size = 2**self.__tree_log_2_size

        if (not self.__w3.isConnected()):
            print("Couldn't connect to node, exiting")
            sys.exit(1)
                            
    def submit_indices_to_logger(self, indices):

        try:
            tx_hash = self.__logger.functions.calculateMerkleRootFromHistory(self.__page_log_2_size, indices).transact({'from': self.__w3.eth.coinbase, 'gas': 6283185})
            tx_receipt = self.__w3.eth.waitForTransactionReceipt(tx_hash)
            if tx_receipt['status'] == 0:
                raise ValueError(receipt['transactionHash'].hex())
            merkle_filter = self.__logger.events.MerkleRootCalculatedFromHistory.createFilter(fromBlock=tx_receipt['blockNumber'])
            merkle_root = merkle_filter.get_all_entries()[0]['args']['_root']
            merkle_log2 = merkle_filter.get_all_entries()[0]['args']['_log2Size']
            merkle_indices = merkle_filter.get_all_entries()[0]['args']['_indices']
            merkle_index = merkle_filter.get_all_entries()[0]['args']['_index']

            if(self.__debug):
                print("root is: " + merkle_root.hex())
                print("log2 is: " + str(merkle_log2))
                print("indices is: " + str(merkle_indices))
                print("index in the history is: " + str(merkle_index))

            return (merkle_index, merkle_root)
        except ValueError as e:
            print("calculateMerkleRoot REVERT transaction: " + str(e))
                            
    def submit_data_to_logger(self, data):

        try:
            tx_hash = self.__logger.functions.calculateMerkleRootFromData(self.__page_log_2_size, data).transact({'from': self.__w3.eth.coinbase, 'gas': 6283185})
            tx_receipt = self.__w3.eth.waitForTransactionReceipt(tx_hash)
            if tx_receipt['status'] == 0:
                raise ValueError(receipt['transactionHash'].hex())
            merkle_filter = self.__logger.events.MerkleRootCalculatedFromData.createFilter(fromBlock=tx_receipt['blockNumber'])
            merkle_root = merkle_filter.get_all_entries()[0]['args']['_root']
            merkle_log2 = merkle_filter.get_all_entries()[0]['args']['_log2Size']
            merkle_data = merkle_filter.get_all_entries()[0]['args']['_data']
            merkle_index = merkle_filter.get_all_entries()[0]['args']['_index']

            if(self.__debug):
                print("root is: " + merkle_root.hex())
                print("log2 is: " + str(merkle_log2))
                print("data is: " + str(merkle_data))
                print("index in the history is: " + str(merkle_index))

            return (merkle_index, merkle_root)
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
                (index, root) = self.submit_data_to_logger(data)
                indices.append(index)
                data = []
                count -= 1

        if(len(data) != 0):
            (index, root) = self.submit_data_to_logger(data)
            indices.append(index)
            count -= 1

        data = []
        for x in range(self.__page_size):
            data.append(bytes(self.__bytes_of_word))

        while(count > 0):
            (index, root) = self.submit_data_to_logger(data)
            indices.append(index)
            count -= 1

        if(len(indices) > 1):
            (index, root) = self.submit_indices_to_logger(indices)
        
        return root
            
    def download_file(self, root, filename):
        
        (succ, data) = self.__recover_data_from_root(root)

        if(succ):
            if(self.__debug):
                print("data is: " + str(data))
            with open(filename, "wb") as f:
                for b in data:
                    f.write(b)
        else:
            print("The Merkle root is not found in the Logger history")