#!/usr/bin/env python3
# Copyright (c) 2019 The PIVX developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
# -*- coding: utf-8 -*-

from io import BytesIO
from struct import pack
import time

from test_framework.address import key_to_p2pkh, wif_to_privkey
from test_framework.authproxy import JSONRPCException
from test_framework.blocktools import create_coinbase, create_block
from test_framework.key import CECKey
from test_framework.messages import CTransaction, CTxIn, CTxOut, COutPoint, COIN
from test_framework.mininode import network_thread_start
from test_framework.pivx_node import PivxTestNode
from test_framework.script import CScript, OP_CHECKSIG
from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import hash256, connect_nodes_bi, p2p_port, bytes_to_hex_str, hex_str_to_bytes

# filter utxos based on first 5 bytes of scriptPubKey
def getDelegatedUtxos(utxos):
    return [x for x in utxos if x["scriptPubKey"][:10] == '76a97b63d1']


class PIVX_ColdStakingTest(BitcoinTestFramework):

    def set_test_params(self):
        self.setup_clean_chain = True
        self.num_nodes = 3
        self.extra_args = [['-staking=1']] * self.num_nodes
        self.extra_args[0].append('-sporkkey=932HEevBSujW2ud7RfB1YF91AFygbBRQj3de3LyaCRqNzKKgWXi')


    def setup_network(self):
        ''' Can't rely on syncing all the nodes when staking=1
        '''
        self.setup_nodes()
        for i in range(self.num_nodes - 1):
            for j in range(i+1, self.num_nodes):
                connect_nodes_bi(self.nodes, i, j)


    def init_test(self):
        title = "*** Starting %s ***" % self.__class__.__name__
        underline = "-" * len(title)
        self.log.info("\n\n%s\n%s\n%s\n", title, underline, self.description)
        self.DEFAULT_FEE = 0.05
        # Setup the p2p connections and start up the network thread.
        self.test_nodes = []
        for i in range(self.num_nodes):
            self.test_nodes.append(PivxTestNode())
            self.test_nodes[i].peer_connect('127.0.0.1', p2p_port(i))

        network_thread_start()  # Start up network handling in another thread

        # Let the test nodes get in sync
        for i in range(self.num_nodes):
            self.test_nodes[i].wait_for_verack()



    def setColdStakingEnforcement(self, fEnable=True):
        new_val = 1563253447 if fEnable else 4070908800
        # update spork 17 and mine 1 more block
        mess = "Enabling" if fEnable else "Disabling"
        mess += " cold staking with SPORK 17..."
        self.log.info(mess)
        res = self.nodes[0].spork("SPORK_17_COLDSTAKING_ENFORCEMENT", new_val)
        self.log.info(res)
        assert (res == "success")
        time.sleep(1)
        self.sync_all()


    def isColdStakingEnforced(self):
        # verify from node[1]
        active = self.nodes[1].spork("active")
        return active["SPORK_17_COLDSTAKING_ENFORCEMENT"]



    def run_test(self):
        self.description = "Performs tests on the Cold Staking P2CS implementation"
        self.init_test()
        LAST_POW_BLOCK = 250
        NUM_OF_INPUTS = 20
        INPUT_VALUE = 50
        INITAL_MINED_BLOCKS = LAST_POW_BLOCK + 1

        # nodes[0] - coin-owner
        # nodes[1] - cold-staker

        # 1) nodes[0] mines 20 blocks. nodes[2] mines all the others.
        # -----------------------------------------------------------
        # Check that SPORK 17 is disabled
        assert (not self.isColdStakingEnforced())
        print("*** 1 ***")
        self.log.info("Mining %d blocks..." % INITAL_MINED_BLOCKS)
        for i in  range(1, INITAL_MINED_BLOCKS+1):
            if i % 10 == 0:
                self.sync_all()
                self.nodes[0].generate(1)
                self.sync_all()
            else:
                self.generateBlock()
            if i % 25 == 0:
                self.log.info("%d Blocks mined." % i)



        # 2) nodes[0] generates a owner address
        #    nodes[1] generates a cold-staking address.
        # ---------------------------------------------
        print("*** 2 ***")
        self.sync_all()
        owner_address = self.nodes[0].getnewaddress()
        self.log.info("Owner Address: %s" % owner_address)
        staker_address = self.nodes[1].getnewstakingaddress()
        self.log.info("Staking Address: %s" % staker_address)


        # 3) Check enforcement.
        # ---------------------
        print("*** 3 ***")
        self.sync_all()
        self.log.info("Creating a stake-delegation tx before cold staking enforcement...")
        try:
            self.nodes[0].delegatestake(staker_address, INPUT_VALUE, owner_address, False, False, True)
            assert(False)
        except JSONRPCException as e:
            assert("The transaction was rejected!" in str(e))
            self.log.info("Good. Cold Staking NOT ACTIVE yet.")
            pass

        # Enable SPORK
        self.setColdStakingEnforcement()
        # double check
        assert (self.isColdStakingEnforced())


        # 4) nodes[0] delegates a number of inputs for nodes[1] to stake em.
        # ------------------------------------------------------------------
        print("*** 4 ***")
        self.log.info("First check warning when using external addresses...")
        try:
            self.nodes[0].delegatestake(staker_address, INPUT_VALUE, "yCgCXC8N5VThhfiaVuKaNLkNnrWduzVnoT")
            assert(False)
        except JSONRPCException as e:
            assert("Set 'fExternalOwner' argument to true, in order to force the stake delegation" in str(e))
            self.log.info("Good. Warning triggered.")
        self.log.info("Now force the use of external address creating (but not sending) the delegation...")
        res = self.nodes[0].rawdelegatestake(staker_address, INPUT_VALUE, "yCgCXC8N5VThhfiaVuKaNLkNnrWduzVnoT", True)
        assert(res is not None and res != "")
        self.log.info("Good. Warning NOT triggered.")
        self.log.info("Now delegate with internal owner address..")
        self.log.info("Try first with a value (5) below the threshold")
        try:
            self.nodes[0].delegatestake(staker_address, 5, owner_address)
            assert(False)
        except JSONRPCException as e:
            assert("Invalid amount" in str(e))
            self.log.info("Nice. it was not possible.")
        self.log.info("Creating %d stake-delegation txes..." % NUM_OF_INPUTS)
        for i in range(NUM_OF_INPUTS):
            res = self.nodes[0].delegatestake(staker_address, INPUT_VALUE, owner_address)
            assert(res != None and res["txid"] != None and res["txid"] != "")
            assert(res["owner_address"] == owner_address)
            assert(res["staker_address"] == staker_address)
        self.generateBlock()
        self.sync_all()
        self.log.info("%d Txes created." % NUM_OF_INPUTS)
        # check balances:
        self.expected_balance = NUM_OF_INPUTS * INPUT_VALUE
        self.checkBalances()


        # 5) check that the owner (nodes[0]) can spend the coins.
        # -------------------------------------------------------
        print("*** 5 ***")
        self.log.info("Spending back one of the delegated UTXOs...")
        delegated_utxos = getDelegatedUtxos(self.nodes[0].listunspent())
        assert(len(delegated_utxos) > 0)
        u = delegated_utxos[0]
        txhash = self.spendUTXOwithNode(u, 0)
        assert(txhash != None)
        self.log.info("Good. Owner was able to spend - tx: %s" % str(txhash))
        self.generateBlock()
        self.sync_all()
        # check balances after spend.
        self.expected_balance -= float(u["amount"])
        self.checkBalances()
        self.log.info("Balances check out after spend")


        # 6) check that the staker CANNOT use the coins to stake yet.
        # He needs to whitelist the owner first.
        # -----------------------------------------------------------
        self.log.info("Staking 15 blocks to mature P2CS stake delegations...")
        for i in range(1, 16):
            self.generateBlock()
            self.sync_all()
            if i % 5 == 0:
                self.log.info("%d Blocks added." % i)
        print("*** 6 ***")
        self.log.info("Trying to generate a cold-stake block before whitelisting the owner...")
        try:
            self.nodes[1].generate(1)
            assert(False)
        except JSONRPCException as e:
            assert ("Couldn't create new block" in str(e))
            self.log.info("Nice. Cold staker was NOT able to create the block yet.")
            pass
        self.log.info("Whitelisting the owner...")
        ret = self.nodes[1].delegatoradd(owner_address)
        assert(ret)
        self.log.info("Delegator address %s whitelisted" % owner_address)


        # 7) check that the staker CANNOT spend the coins.
        # ------------------------------------------------
        print("*** 7 ***")
        self.log.info("Trying to spend one of the delegated UTXOs with the cold-staking key...")
        delegated_utxos = getDelegatedUtxos(self.nodes[0].listunspent())
        assert(len(delegated_utxos) > 0)
        u = delegated_utxos[0]
        try:
            self.spendUTXOwithNode(u, 1)
            assert(False)
        except JSONRPCException as e:
            assert("Script failed an OP_CHECKCOLDSTAKEVERIFY operation" in str(e))
            self.log.info("Good. Cold staker was NOT able to spend (failed OP_CHECKCOLDSTAKEVERIFY)")
            pass
        self.generateBlock()
        self.sync_all()


        # 8) check that the staker can use the coins to stake a block with internal miner.
        # --------------------------------------------------------------------------------
        print("*** 8 ***")
        self.log.info("Generating one valid cold-stake block...")
        self.nodes[1].generate(1)
        self.log.info("New block created by cold-staking. Trying to submit...")
        newblockhash = self.nodes[1].getbestblockhash()
        self.log.info("Block %s submitted" % newblockhash)
        # Verify that nodes[0] accepts it
        self.sync_all()
        self.nodes[0].getblock(newblockhash)
        self.log.info("Great. Cold-staked block was accepted!")
        # check balances after staked block.
        self.expected_balance += 250
        self.checkBalances()
        self.log.info("Balances check out after staked block")


        # 9) check that the staker can use the coins to stake a block with a rawtransaction.
        # ----------------------------------------------------------------------------------
        print("*** 9 ***")
        self.log.info("Generating another valid cold-stake block...")
        stakeable_coins = getDelegatedUtxos(self.nodes[0].listunspent())
        block_n = self.nodes[1].getblockcount()
        block_hash = self.nodes[1].getblockhash(block_n)
        prevouts = self.get_prevouts(stakeable_coins, 1)
        assert(len(prevouts) > 0)
        # Create the block
        new_block = self.create_block(block_hash, prevouts, block_n+1, 1, staker_address)
        self.log.info("New block created (rawtx) by cold-staking. Trying to submit...")
        # Try to submit the block
        ret = self.nodes[1].submitblock(bytes_to_hex_str(new_block.serialize()))
        self.log.info("Block %s submitted." % new_block.hash)
        assert(ret is None)
        # Verify that nodes[0] accepts it
        self.sync_all()
        self.nodes[0].getblock(new_block.hash)
        self.log.info("Great. Cold-staked block was accepted!")
        # check balances after staked block.
        self.expected_balance += 250
        self.checkBalances()
        self.log.info("Balances check out after staked block")


        # 10) check that the staker cannot stake a block changing the coinstake scriptPubkey.
        # ----------------------------------------------------------------------------------
        print("*** 10 ***")
        self.log.info("Generating one invalid cold-stake block (changing first coinstake output)...")
        stakeable_coins = getDelegatedUtxos(self.nodes[0].listunspent())
        block_n = self.nodes[1].getblockcount()
        block_hash = self.nodes[1].getblockhash(block_n)
        prevouts = self.get_prevouts(stakeable_coins, 1)
        assert(len(prevouts) > 0)
        # Create the block
        new_block = self.create_block(block_hash, prevouts, block_n+1, 1, staker_address, fInvalid=1)
        self.log.info("New block created (rawtx) by cold-staking. Trying to submit...")
        # Try to submit the block
        ret = self.nodes[1].submitblock(bytes_to_hex_str(new_block.serialize()))
        self.log.info("Block %s submitted." % new_block.hash)
        assert("rejected" in ret)
        # Verify that nodes[0] rejects it
        self.sync_all()
        try:
            self.nodes[0].getblock(new_block.hash)
        except JSONRPCException as e:
            assert("Block not found" in str(e))
            self.log.info("Great. Malicious cold-staked block was NOT accepted!")
            pass


        # 11) neither adding different outputs to the coinstake.
        # ------------------------------------------------------
        print("*** 11 ***")
        self.log.info("Generating another invalid cold-stake block (adding coinstake output)...")
        stakeable_coins = getDelegatedUtxos(self.nodes[0].listunspent())
        block_n = self.nodes[1].getblockcount()
        block_hash = self.nodes[1].getblockhash(block_n)
        prevouts = self.get_prevouts(stakeable_coins, 1)
        assert(len(prevouts) > 0)
        # Create the block
        new_block = self.create_block(block_hash, prevouts, block_n+1, 1, staker_address, fInvalid=2)
        self.log.info("New block created (rawtx) by cold-staking. Trying to submit...")
        # Try to submit the block
        ret = self.nodes[1].submitblock(bytes_to_hex_str(new_block.serialize()))
        self.log.info("Block %s submitted." % new_block.hash)
        assert("rejected" in ret)
        # Verify that nodes[0] rejects it
        self.sync_all()
        try:
            self.nodes[0].getblock(new_block.hash)
        except JSONRPCException as e:
            assert("Block not found" in str(e))
            self.log.info("Great. Malicious cold-staked block was NOT accepted!")
            pass


        # 12) Now node[0] gets mad and spends all the delegated coins, voiding the P2CS contracts.
        # ----------------------------------------------------------------------------------------
        self.log.info("Let's void the contracts.")
        self.generateBlock()
        self.sync_all()
        print("*** 12 ***")
        self.log.info("Cancel the stake delegation spending the cold stakes...")
        delegated_utxos = getDelegatedUtxos(self.nodes[0].listunspent())
        # remove one utxo to spend later
        final_spend = delegated_utxos.pop()
        txhash = self.spendUTXOsWithNode(delegated_utxos, 0)
        assert(txhash != None)
        self.log.info("Good. Owner was able to void the stake delegations - tx: %s" % str(txhash))
        self.generateBlock()
        self.sync_all()
        # deactivate SPORK 17 and check that the owner can still spend the last utxo
        self.setColdStakingEnforcement(False)
        assert (not self.isColdStakingEnforced())
        txhash = self.spendUTXOsWithNode([final_spend], 0)
        assert(txhash != None)
        self.log.info("Good. Owner was able to void the last stake delegation (with SPORK 17 disabled) - tx: %s" % str(txhash))
        self.generateBlock()
        self.sync_all()
        # check balances after big spend.
        self.expected_balance = 2 * (INPUT_VALUE + 250)
        self.checkBalances()
        self.log.info("Balances check out after the delegations have been voided.")
        # re-activate SPORK17
        self.setColdStakingEnforcement()
        assert (self.isColdStakingEnforced())


        # 13) check that coinstaker is empty and can no longer stake.
        # -----------------------------------------------------------
        print("*** 13 ***")
        self.log.info("Trying to generate one cold-stake block again...")
        try:
            self.nodes[1].generate(1)
            assert(False)
        except JSONRPCException as e:
            assert ("Couldn't create new block" in str(e))
            self.log.info("Cigar. Cold staker was NOT able to create any more blocks.\n")
            pass


    def generateBlock(self):
        fStaked = False
        while (not fStaked):
            try:
                self.nodes[2].generate(1)
                fStaked = True
            except JSONRPCException as e:
                if ("Couldn't create new block" in str(e)):
                    # Sleep two seconds and retry
                    time.sleep(2)
                else:
                    raise e




    def checkBalances(self):
        w_info = self.nodes[0].getwalletinfo()
        self.log.info("OWNER - Delegated %f / Cold %f" % (
            float(w_info["delegated_balance"]), w_info["cold_staking_balance"]))
        assert(float(w_info["delegated_balance"]) == self.expected_balance)
        assert (float(w_info["cold_staking_balance"]) == 0)
        w_info = self.nodes[1].getwalletinfo()
        self.log.info("STAKER - Delegated %f / Cold %f" % (
            float(w_info["delegated_balance"]), w_info["cold_staking_balance"]))
        assert(float(w_info["delegated_balance"]) == 0)
        assert (float(w_info["cold_staking_balance"]) == self.expected_balance)



    def spendUTXOwithNode(self, utxo, node_n):
        new_addy = self.nodes[node_n].getnewaddress()
        inputs = [{"txid": utxo["txid"], "vout": utxo["vout"]}]
        out_amount = (float(utxo["amount"]) - self.DEFAULT_FEE)
        outputs = {}
        outputs[new_addy] = out_amount
        spendingTx = self.nodes[node_n].createrawtransaction(inputs, outputs)
        spendingTx_signed = self.nodes[node_n].signrawtransaction(spendingTx)
        return self.nodes[node_n].sendrawtransaction(spendingTx_signed["hex"])


    def spendUTXOsWithNode(self, utxos, node_n):
        new_addy = self.nodes[node_n].getnewaddress()
        inputs = []
        outputs = {}
        outputs[new_addy] = 0
        for utxo in utxos:
            inputs.append({"txid": utxo["txid"], "vout": utxo["vout"]})
            outputs[new_addy] += float(utxo["amount"])
        outputs[new_addy] -= self.DEFAULT_FEE
        spendingTx = self.nodes[node_n].createrawtransaction(inputs, outputs)
        spendingTx_signed = self.nodes[node_n].signrawtransaction(spendingTx)
        return self.nodes[node_n].sendrawtransaction(spendingTx_signed["hex"])



    def get_prevouts(self, coins, node_n, confs=15):
        api = self.nodes[node_n]
        prevouts = {}
        for utxo in coins:
            prevtx = api.getrawtransaction(utxo['txid'], 1)
            prevScript = prevtx['vout'][utxo['vout']]['scriptPubKey']['hex']
            prevtx_btime = prevtx['blocktime']
            prevtx_bhash = prevtx['blockhash']
            modifier = 0
            if utxo['confirmations'] < confs:
                continue
            o = COutPoint(int(utxo['txid'], 16), utxo['vout'])
            prevouts[o] = (int(utxo['amount']) * COIN, prevtx_btime, modifier, prevScript)
        return prevouts



    def create_block(self, prev_hash, staking_prevouts, height, node_n, s_address, fInvalid=0):
        api = self.nodes[node_n]
        # Get current time
        current_time = int(time.time())
        nTime = current_time & 0xfffffff0

        # Create coinbase TX
        coinbase = create_coinbase(height)
        coinbase.vout[0].nValue = 0
        coinbase.vout[0].scriptPubKey = b""
        coinbase.nTime = nTime
        coinbase.rehash()

        # Create Block with coinbase
        block = create_block(int(prev_hash, 16), coinbase, nTime)

        # Find valid kernel hash - Create a new private key used for block signing.
        if not block.solve_stake(staking_prevouts):
            raise Exception("Not able to solve for any prev_outpoint")

        # Create coinstake TX
        amount, prev_time, modifier, prevScript = staking_prevouts[block.prevoutStake]
        outNValue = int(amount + 250 * COIN)
        stake_tx_unsigned = CTransaction()
        stake_tx_unsigned.nTime = block.nTime
        stake_tx_unsigned.vin.append(CTxIn(block.prevoutStake))
        stake_tx_unsigned.vin[0].nSequence = 0xffffffff
        stake_tx_unsigned.vout.append(CTxOut())
        stake_tx_unsigned.vout.append(CTxOut(outNValue, hex_str_to_bytes(prevScript)))

        if fInvalid == 1:
            # Create a new private key and get the corresponding public key
            block_sig_key = CECKey()
            block_sig_key.set_secretbytes(hash256(pack('<I', 0xffff)))
            pubkey = block_sig_key.get_pubkey()
            stake_tx_unsigned.vout[1].scriptPubKey = CScript([pubkey, OP_CHECKSIG])
        else:
            # Export the staking private key to sign the block with it
            privKey, compressed = wif_to_privkey(api.dumpprivkey(s_address))
            block_sig_key = CECKey()
            block_sig_key.set_compressed(compressed)
            block_sig_key.set_secretbytes(bytes.fromhex(privKey))
            # check the address
            addy = key_to_p2pkh(bytes_to_hex_str(block_sig_key.get_pubkey()), False, True)
            assert (addy == s_address)
            if fInvalid == 2:
                # add a new output with 100 coins from the pot
                new_key = CECKey()
                new_key.set_secretbytes(hash256(pack('<I', 0xffff)))
                pubkey = new_key.get_pubkey()
                stake_tx_unsigned.vout.append(CTxOut(100 * COIN, CScript([pubkey, OP_CHECKSIG])))
                stake_tx_unsigned.vout[1].nValue = outNValue - 100 * COIN

        # Sign coinstake TX and add it to the block
        stake_tx_signed_raw_hex = api.signrawtransaction(bytes_to_hex_str(stake_tx_unsigned.serialize()))['hex']
        stake_tx_signed = CTransaction()
        stake_tx_signed.deserialize(BytesIO(hex_str_to_bytes(stake_tx_signed_raw_hex)))
        block.vtx.append(stake_tx_signed)

        # Get correct MerkleRoot and rehash block
        block.hashMerkleRoot = block.calc_merkle_root()
        block.rehash()

        # sign block with block signing key and return it
        block.sign_block(block_sig_key)
        return block


if __name__ == '__main__':
    PIVX_ColdStakingTest().main()