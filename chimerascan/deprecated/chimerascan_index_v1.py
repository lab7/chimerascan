#!/usr/bin/env python
'''
Created on Jan 5, 2011

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
import logging
import os
import shutil
import subprocess
import sys
from optparse import OptionParser

# local imports
import chimerascan.pysam as pysam
from chimerascan.lib.feature import GeneFeature
from chimerascan.lib.seq import DNA_reverse_complement
from chimerascan.lib.config import JOB_ERROR, JOB_SUCCESS, ALIGN_INDEX, GENE_REF_PREFIX, GENE_FEATURE_FILE
from chimerascan.lib.base import check_executable

BASES_PER_LINE = 50

def split_seq(seq, chars_per_line):
    pos = 0
    newseq = []
    while pos < len(seq):
        if pos + chars_per_line > len(seq):        
            endpos = len(seq)
        else:
            endpos = pos + chars_per_line
        newseq.append(seq[pos:endpos])
        pos = endpos
    return '\n'.join(newseq)

def bed12_to_fasta(gene_feature_file, reference_seq_file):
    ref_fa = pysam.Fastafile(reference_seq_file)
    for g in GeneFeature.parse(open(gene_feature_file)):
        exon_seqs = []
        error_occurred = False
        for start, end in g.exons:
            seq = ref_fa.fetch(g.chrom, start, end)
            if not seq:
                logging.warning("gene %s exon %s:%d-%d not found in reference" % 
                                (g.tx_name, g.chrom, start, end))
                error_occurred = True
                break
            exon_seqs.append(seq)
        if error_occurred:
            continue
        # make fasta record
        seq = ''.join(exon_seqs)
        if g.strand == '-':
            seq = DNA_reverse_complement(seq)
        # break seq onto multiple lines
        seqlines = split_seq(seq, BASES_PER_LINE)    
        yield (">%s range=%s:%d-%d gene=%s strand=%s\n%s" % 
               (GENE_REF_PREFIX + g.tx_name, g.chrom, start, end, g.strand, g.gene_name, seqlines))
    ref_fa.close()

def create_chimerascan_index(output_dir, genome_fasta_file, 
                             gene_feature_file,
                             bowtie_build_bin):
    # create output dir if it does not exist
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        logging.info("Created index directory: %s" % (output_dir))
    # create FASTA index file
    index_fasta_file = os.path.join(output_dir, ALIGN_INDEX + ".fa")
    fh = open(index_fasta_file, "w")
    # copy reference fasta file to output dir
    logging.info("Adding reference genome to index...")
    shutil.copyfileobj(open(genome_fasta_file), fh)
    # extract sequences from gene feature file
    logging.info("Adding gene models to index...")
    for fa_record in bed12_to_fasta(gene_feature_file, genome_fasta_file):
        print >>fh, fa_record
    fh.close()
    # copy gene bed file to index directory
    shutil.copyfile(gene_feature_file, os.path.join(output_dir, GENE_FEATURE_FILE))
    # index the combined fasta file
    logging.info("Indexing FASTA file...")
    fh = pysam.Fastafile(index_fasta_file)
    fh.close()
    # build bowtie index on the combined fasta file
    logging.info("Building bowtie index...")
    bowtie_index_name = os.path.join(output_dir, ALIGN_INDEX)
    args = [bowtie_build_bin, index_fasta_file, bowtie_index_name]
    if subprocess.call(args) != os.EX_OK:
        logging.error("bowtie-build failed to create alignment index")
        return JOB_ERROR
    logging.info("chimerascan index created successfully")
    return JOB_SUCCESS

def main():
    logging.basicConfig(level=logging.DEBUG,
                        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    parser = OptionParser("usage: %prog [options] <reference_genome.fa> <gene_models.txt> <index_output_dir>")
    parser.add_option("--bowtie-build-bin", dest="bowtie_build_bin", default="bowtie-build", 
                      help="Path to 'bowtie-build' program")
    options, args = parser.parse_args()
    # check command line arguments
    if len(args) < 3:
        parser.error("Incorrect number of command line arguments")
    ref_fasta_file = args[0]
    gene_feature_file = args[1]
    output_dir = args[2]
    # check that input files exist
    if not os.path.isfile(ref_fasta_file):
        parser.error("Reference fasta file '%s' not found" % (ref_fasta_file))
    if not os.path.isfile(gene_feature_file):
        parser.error("Gene feature file '%s' not found" % (gene_feature_file))
    # check that output dir is not a regular file
    if os.path.exists(output_dir) and (not os.path.isdir(output_dir)):
        parser.error("Output directory name '%s' exists and is not a valid directory" % (output_dir))
    # check that bowtie-build program exists
    if check_executable(options.bowtie_build_bin):
        logging.debug("Checking for 'bowtie-build' binary... found")
    else:
        parser.error("bowtie-build binary not found or not executable")
    # run main index creation function
    retcode = create_chimerascan_index(output_dir, ref_fasta_file, gene_feature_file,
                                       options.bowtie_build_bin)
    sys.exit(retcode)

if __name__ == '__main__':
    main()