'''
Created on Apr 29, 2011

@author: mkiyer

chimerascan: chimeric transcript discovery using RNA-seq

Copyright (C) 2011 Matthew Iyer

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
'''
import array
import logging
import subprocess
import sys

from chimerascan.lib.sam import parse_pe_reads, get_insert_size
from chimerascan import pysam

class InsertSizeDistribution(object):
    def __init__(self):
        self.min_isize = None
        self.max_isize = None
        self.arr = None

    def percentile(self, per):
        n = sum(self.arr)
        per_n = n * per / 100.0
        count = 0
        for isize,x in enumerate(self.arr): 
            count += x
            if (count >= per_n):
                break
        return isize + self.min_isize

    @property
    def n(self):
        if self.arr is None: return 0
        return sum(self.arr)
    
    def mode(self):
        return self.arr.index(max(self.arr)) + self.min_isize

    def mean(self):
        count = 0
        n = 0        
        for i,x in enumerate(self.arr): 
            count += i*x
            n += x
        if n == 0:
            return None            
        return self.min_isize + (count / float(n))
    
    def std(self):
        mean = self.mean()
        if mean is None:
            return None
        n = 0
        std = 0
        for i,x in enumerate(self.arr):
            std = std + x*((i - mean)**2)
            n += x
        std = (std / float(n-1))**0.5
        return std

    def to_file(self, fileh):
        print >>fileh, '\t'.join(["#insert_size", "num_samples"])
        for i,x in enumerate(self.arr):
            print >>fileh, '\t'.join([str(i + self.min_isize), str(x)])        

    def from_file(self, fileh):
        isizes = []
        counts = []
        for line in fileh:
            if line.startswith("#"):
                continue
            fields = line.strip().split('\t')
            i,x = map(int, fields[0:2])
            isizes.append(i)
            counts.append(x)
        self.min_isize = isizes[0]
        self.max_isize = isizes[-1]
        self.arr = array.array('L', counts) 

    @staticmethod
    def from_bam(bamfh, min_isize, max_isize, max_samples=None):
        """
        iterates through a BAM file looking for uniquely mapping concordant
        reads.  keeps a histogram of all observed insert sizes in the
        reads.  stops once 'max_samples' valid reads are encountered, or
        the end of the file is reached
        """
        res = InsertSizeDistribution()
        res.min_isize = min_isize
        res.max_isize = max_isize
        res.arr = array.array('L', (0 for x in xrange(min_isize, max_isize+1)))        
        count = 0
        outside_range = 0
        unmapped = 0
        multimapping = 0
        discordant = 0
        # setup debugging logging messages
        debug_count = 0
        debug_every = 1e5
        debug_next = debug_every
        for pe_reads in parse_pe_reads(bamfh):
            # progress log
            debug_count += 1
            if debug_count == debug_next:
                debug_next += debug_every
                logging.debug("Processed reads: %d" % (debug_count))
                logging.debug("Unique paired reads: %d" % (count))
                logging.debug("Unmapped: %d" % (unmapped))
                logging.debug("Ambiguous (multimapping): %d" % (multimapping))
                logging.debug("Outside range: %d" % (outside_range))
            if (max_samples is not None) and count > max_samples:
                break
            # only use uniquely mapping reads on the same chromosome
            num_read1_mappings = len(pe_reads[0])
            num_read2_mappings = len(pe_reads[1])
            if (num_read1_mappings == 0) or (num_read2_mappings == 0):
                unmapped += 1
                if num_read1_mappings > 0:
                    print pe_reads[0][0]
                if num_read2_mappings > 0:
                    print pe_reads[1][0]
                continue
            if (num_read1_mappings > 1) or (num_read2_mappings > 1):
                multimapping += 1
                continue
            # each read has exactly one alignment
            r1 = pe_reads[0][0]
            r2 = pe_reads[1][0]
            if r1.rname != r2.rname:
                discordant += 1
                continue
            # compute insert size
            isize = get_insert_size(r1, r2)
            if (res.min_isize <= isize <= res.max_isize):
                # store in array
                res.arr[isize - res.min_isize] += 1
                count += 1
            else:
                outside_range += 1
        return res

def main():
    from optparse import OptionParser
    logging.basicConfig(level=logging.DEBUG,
                        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    parser = OptionParser("usage: %prog [options] <bam> <out.bedpe>")
    parser.add_option('-i', '--min-fragment-length', dest="min_fragment_length", 
                      type="int", default=0)
    parser.add_option('-I', '--max-fragment-length', dest="max_fragment_length", 
                      type="int", default=1000)
    parser.add_option('-n', '--max-samples', dest="max_samples", 
                      type="int", default=None)
    parser.add_option('-o', dest="output_file", default=None) 
    options, args = parser.parse_args()
    input_bam_file = args[0]
    bamfh = pysam.Samfile(input_bam_file, "rb")
    isizedist = InsertSizeDistribution.from_bam(bamfh, 
                                                options.min_fragment_length, 
                                                options.max_fragment_length, 
                                                options.max_samples)
    bamfh.close()
    if options.output_file is not None:
        f = open(options.output_file, "w")
    else:
        f = sys.stdout
    isizedist.to_file(f)
    if options.output_file is not None:
        f.close()
    logging.info("Insert size samples=%d mean=%f std=%f median=%d mode=%d" % 
                 (isizedist.n, isizedist.mean(), isizedist.std(), 
                  isizedist.percentile(50.0), isizedist.mode()))

if __name__ == '__main__':
    main()
