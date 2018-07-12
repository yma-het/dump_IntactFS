#!/usr/bin/env python3

from tabulate import tabulate
from collections import OrderedDict
from typing import List
from os.path import basename
from os import mkdir
import logging
import sys

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

FILENAME_SZ = 0xD
EXT_ATTR_1_SZ = 0x3
FILE_LEN_SZ = 0x4
SPACER_1_SZ = 0x5
EXT_ATTR_2_SZ = 0x2
SPACER_2_SZ = 0x5


BLOCK_SZ = 0x1080
PAGE_SZ = 0x200
DELIM_SZ = 0x10

FS_ROOT_DIR_OFFSET = 0x8
FS_ROOT_DIR_RESERVED = 0x20


READ_CHUNK_SIZE = PAGE_SZ


class DelimiterReachedError(Exception):
    def __init__(self, reached_at_pos, bytes_requested):
        self.reached_at_pos = reached_at_pos
        self.bytes_requested = bytes_requested

    def __repr__(self):
        msg = ("Reached delimiter! Position, where it has been reached: {}, "
               "reqested to read {} bytes.")
        return msg.format(self.reached_at_pos, self.bytes_requested)


class FilenameDecodeError(Exception):
    def __init__(self, bytes_buff):
        self.bytes_buff = bytes_buff

    def __repr__(self):
        msg = ("Error encountered, while trying to decode filename!"
               "Decode buffer: {}".format(bytes(self.bytes_buff)))
        return msg


class DelimiterInspector():
    def __init__(self, path, mode):
        self.path = path
        self.mode = mode

    def __enter__(self):
        self.fd = open(self.path, self.mode)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.fd.close()

    def _inspect_delimiters(self, sz):
        PAGE_AND_DELIMITER = PAGE_SZ + DELIM_SZ
        before_next_delimiter = (
            PAGE_AND_DELIMITER - self.tell() % PAGE_AND_DELIMITER - DELIM_SZ)
        if sz > before_next_delimiter:
            raise DelimiterReachedError(self.tell() + before_next_delimiter, sz)

    def tell(self):
        return self.fd.tell()

    def read(self, sz):
        self._inspect_delimiters(sz)
        return self.fd.read(sz)

    def seek(self, sz, from_what):
        # just because _inspect_delimiters cannot handle this
        if from_what not in [0, 1]:
            raise NotImplementedError(
                "Class {} does not support seeks form EOF!".format(
                    self.__class__)
                )
        return self.fd.seek(sz, from_what)


class DelimiterAutoSkipper():
    def __init__(self, delimiter_inspector: DelimiterInspector):
        self.__delimiter_inspector = delimiter_inspector

    def _sklip_delimiter(self):
        self.__delimiter_inspector.seek(DELIM_SZ, 1)

    def _tell(self) -> int:
        return self.__delimiter_inspector.tell()

    def read(self, sz: int) -> bytes:
        result = bytes()
        while not len(result) == sz:
            try:
                result += self.__delimiter_inspector.read(sz)
            except DelimiterReachedError as e:
                bytes_before_delimiter = e.reached_at_pos - self._tell()
                result += self.__delimiter_inspector.read(bytes_before_delimiter)
                self._sklip_delimiter()
        return result


def read_file_index_table(fo: DelimiterInspector) -> List[OrderedDict]:
    # yeah, this is bad manner to have func definitions inside func,
    # but for what we will show them in global scope?
    def parse_file_name(raw_fname):
        i = FILENAME_SZ - 1
        try:
            while raw_fname[i] == 0:
                i -= 1
        except IndexError:
            raise FilenameDecodeError(raw_fname)
        return str(raw_fname[:i+1], "ASCII")

    def read_and_convert_to_hex(file_descriptor, size):
        return "0x" + bytes(file_descriptor.read(size)).hex().upper()

    record = OrderedDict()
    while True:
        record['file_name'] = parse_file_name(fo.read(FILENAME_SZ))
        record['ext_attr_1'] = read_and_convert_to_hex(fo, EXT_ATTR_1_SZ)
        record['file_len'] = int.from_bytes(
            fo.read(FILE_LEN_SZ), byteorder='big')
        fo.seek(SPACER_1_SZ, 1)
        record['offset'] = read_and_convert_to_hex(fo, EXT_ATTR_2_SZ)
        fo.seek(SPACER_2_SZ, 1)
        if record['file_name']:
            yield record
        else:
            break


def get_file_index_table(fs_dump: DelimiterInspector) -> List:
    file_index_offset = FS_ROOT_DIR_OFFSET*BLOCK_SZ+FS_ROOT_DIR_RESERVED
    fs_dump.seek(file_index_offset, 0)
    fs_index = []
    file_index_table_reader_irerator = read_file_index_table(fs_dump)
    while True:
        try:
            file_index_record = OrderedDict(next(file_index_table_reader_irerator))
        # TODO: write correct fs index end condition!!!
        except (StopIteration, FilenameDecodeError):
            break
        except DelimiterReachedError:
            fs_dump.seek(DELIM_SZ, 1)
            file_index_table_reader_irerator = read_file_index_table(fs_dump)
            continue
        fs_index.append(file_index_record)
    return fs_index

def print_fs_index(fs_index: List):
    if not fs_index:
        logger.info("No valid filesystem records was detected!")
    header = fs_index[0].keys()
    rows = [x.values() for x in fs_index]
    logger.info("\n" + tabulate(rows, header))


def str_to_hex(s: str) -> int:
    return int(s, base=16)

dump_path =  "../NAND_AUTO_2534.BIN"
results_dir = "../unpack_{}".format(basename(dump_path))
mkdir(results_dir)
results_dir = results_dir + "/{}"

with DelimiterInspector(dump_path, "rb") as fs_dump:

    fs_index = get_file_index_table(fs_dump)
    print_fs_index(fs_index)
    delimiter_auto_skipper = DelimiterAutoSkipper(fs_dump)
    for file_rec in fs_index:
        block_offset = str_to_hex(file_rec["offset"]) >> 4
        offset = block_offset * BLOCK_SZ
        file_len = file_rec["file_len"]
        to_be_readen = file_len
        fs_dump.seek(offset, 0)
        file_rec['file_name'] = file_rec["file_name"].replace("\x00", "_")

        #logger.info("dumping {} file...".format(str(bytes(file_rec['file_name']))))
        with open(results_dir.format(file_rec['file_name']), "wb+") as curr_file:
            while to_be_readen > 0:
                chunk = delimiter_auto_skipper.read(min(READ_CHUNK_SIZE, to_be_readen))
                curr_file.write(chunk)
                to_be_readen -= len(chunk)
