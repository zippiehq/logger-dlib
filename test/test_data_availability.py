import json
import filecmp
from data_availability import DataAvailability

# start of main test

with open('../build/contracts/DataAvailability.json') as json_file:
    da_data = json.load(json_file)

with open('./deployedAddresses.json') as json_file:
    deployed_address = json.load(json_file)

# TODO: Make endpoint and page_log2_size tree_log2_size configurable
da = DataAvailability("http://127.0.0.1:8545", deployed_address["da_address"], da_data['abi'])
da.instantiate(2, 5)

# test case 1
data = []
data.append(bytes("est95192", 'utf-8'))
data.append(bytes("51e5q1w9", 'utf-8'))
data.append(bytes("54sd984s", 'utf-8'))
data.append(bytes("df5a1ste", 'utf-8'))

(index_1, root) = da.submit_data_to_da(data)

assert root == bytes.fromhex("41635d7ab5ba446d0ffa701662a60aec0709b0f778f15745a631d070ccfa90f4"), "Hashes not match"

# test case 2
data = []
data.append(bytes("st951925", 'utf-8'))
data.append(bytes("1e5sdqsa", 'utf-8'))
data.append(bytes("12325245", 'utf-8'))
data.append(bytes("99541234", 'utf-8'))

(index_2, root) = da.submit_data_to_da(data)

assert root == bytes.fromhex("3a908ab397101eeb698596301f50dbee1c20ec57989bf5d3f87f71eafe55730a"), "Hashes not match"

# test case 3
indices = []
indices.append(index_1)
indices.append(index_2)
indices.append(index_1)
indices.append(index_2)
indices.append(index_1)
indices.append(index_2)
indices.append(index_1)
indices.append(index_2)

(index_3, root) = da.submit_indices_to_da(indices)

assert root == bytes.fromhex("599b88906b87ebe8c111c26198887c218de8b16a1963b9d3a0f6eb02107c4f24"), "Hashes not match"

# test case 4
input_file = "test_file"
output_file = "recovered_file"

root = da.upload_file(input_file)
da.download_file(root, output_file)

assert filecmp.cmp(input_file, output_file), "Files not match"

da.instantiate(3, 5)

# test case 5
data = []
data.append(bytes("est95192", 'utf-8'))
data.append(bytes("51e5q1w9", 'utf-8'))
data.append(bytes("54sd984s", 'utf-8'))
data.append(bytes("df5a1ste", 'utf-8'))
data.append(bytes("st951925", 'utf-8'))
data.append(bytes("1e5sdqsa", 'utf-8'))
data.append(bytes("12325245", 'utf-8'))
data.append(bytes("99541234", 'utf-8'))

(index_3, root) = da.submit_data_to_da(data)

assert root == bytes.fromhex("2bf37c10b1fd8c140f259f4fbbc5a6cc090cffd7edc7e4b8a4e53db7020876b6"), "Hashes not match"

# test case 6
indices = []
indices.append(index_3)
indices.append(index_3)
indices.append(index_3)
indices.append(index_3)

(index_3, root) = da.submit_indices_to_da(indices)

assert root == bytes.fromhex("599b88906b87ebe8c111c26198887c218de8b16a1963b9d3a0f6eb02107c4f24"), "Hashes not match"

# test case 7
input_file = "test_file"
output_file = "recovered_file"

root = da.upload_file(input_file)
da.download_file(root, output_file)

assert filecmp.cmp(input_file, output_file), "Files not match"

# end of test
print("All tests passed!")