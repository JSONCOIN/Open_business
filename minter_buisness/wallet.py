import time
from decimal import Decimal

from mintersdk.sdk.transactions import MinterSellCoinTx, MinterSellAllCoinTx, MinterSendCoinTx, MinterMultiSendCoinTx
from mintersdk.sdk.wallet import MinterWallet
from mintersdk.shortcuts import to_bip

from sdk.delegators import Delegators
from sdk.settings import API


class Wallet:

    def __init__(self, seed):
        self.seed = seed
        self.private_key = MinterWallet.create(mnemonic=seed)['private_key']
        self.address = MinterWallet.create(mnemonic=seed)['address']

    # ------------------------------------------
    # ОСНОВНЫЕ ФУНКЦИИ
    # ------------------------------------------

    def get_balance(self, in_bip=True):
        """
        Получаем баланс кошелька
        """
        return API.get_balance(self.address, pip2bip=in_bip)['result']['balance']

    def get_bip_balance(self):
        """
        Получаем баланс кошелька в BIP
        """
        return API.get_balance(self.address, pip2bip=True)['result']['balance']

    def convert(self, value, from_symbol, to_symbol):
        """
        Конвертирует одну монету в другую
        :param value: int/float
        :param from_symbol: str (Тикер монеты)
        :param to_symbol: str (Тикер монеты)
        :return:
        """

        from_symbol = from_symbol.upper()
        to_symbol = to_symbol.upper()
        value = Decimal(str(value))

        balances = self.get_balance(in_bip=True)

        if balances[from_symbol] <= value:
            print(f"На кошельке недостаточно {from_symbol}")
            return

        # Генерируем транзакцию
        nonce = API.get_nonce(self.address)
        tx = MinterSellCoinTx(
            coin_to_sell=from_symbol,
            value_to_sell=value,
            coin_to_buy=to_symbol,
            min_value_to_buy=0,
            nonce=nonce,
            gas_coin=from_symbol
        )

        # Проверяем достаточно ли баланса на оплату комиссии
        commission = to_bip(tx.get_fee())
        if balances[from_symbol] <= (value + commission):
            print(f"На кошельке недостаточно {from_symbol} для оплаты комиссии {commission}\n"
                  f"Баланс: {round(balances[from_symbol], 2)}\n"
                  f"Нужно:  {value + commission} (+{value + commission - round(balances[from_symbol], 2)})")
            return

        # Отправляем транзакицю
        tx.sign(private_key=self.private_key)
        return API.send_transaction(tx.signed_tx)

    def convert_all_coins_to(self, symbol):
        """
        Конвертирует все монеты на кошельке в symbol
        """
        symbol = symbol.upper()
        balances = self.get_balance()

        if self._only_symbol(balances, symbol):
            return

        del (balances[symbol])
        for i, coin in enumerate(balances, 1):
            if coin == symbol:
                continue

            nonce = API.get_nonce(self.address)
            tx = MinterSellAllCoinTx(
                coin_to_sell=coin, coin_to_buy=symbol, min_value_to_buy=0, nonce=nonce, gas_coin=coin
            )
            tx.sign(private_key=self.private_key)
            API.send_transaction(tx.signed_tx)

            print(f'{coin} сконвертирован в {symbol}')

            if i != len(balances):
                self._wait_for_nonce(nonce)

    def pay(self, payouts, coin="BIP", payload='', include_commission=True):
        """
        Выплата на любое количество адресов
        :param payouts: dict > {'Mp...1: 100', 'Mp...2': 50, ...} - словарь кошелек: сумма
        :param coin: str > 'SYMBOL' - Монета, в которой будет производится выплата
        :param payload: str - комментарий к транзакции
        :param include_commission: bool - Если True, то комиссия за перевод включается в сумму выплаты и выплаты будут пересчитаны с учетом комиссии
        :return: json - ответ от ноды
        """
        return self.multisend(payouts, coin=coin, payload=payload, include_commission=include_commission)

    def pay_token_delegators(self, delegated_token, to_be_payed, by_node='', min_delegated=0, stop_list=None, coin='BIP', payload='', include_commission=True):
        """
        Выплата делегаторам конкретного токена
        :param delegated_token: str > 'SYMBOL' - делгаторы этого токена получают выплату
        :param to_be_payed: int/float - сумма, которая будет выплачена всем делегаторам
        :param by_node: str > 'Mp....' - публичный адрес валидатора . Если заполнить, то выплата будет только делегатором конкретной ноды
        :param min_delegated: int/float - столько минимум должно быть делегировано, чтобы получить выплату
        :param stop_list: list > ['Mx...1', 'Mx...2', ...] кошельки, не участвующие в выплате
        :param coin: str > 'SYMBOL' - монета, в которой будет производится выплата
        :param payload: str - комментарий к транзакции
        :param include_commission: bool - Если True, то комиссия за перевод включается в сумму выплаты и выплаты будут пересчитаны с учетом комиссии
        :return:
        """
        delegators = Delegators(delegated_token)
        payouts = delegators.get_payouts(to_be_payed, by_node=by_node, min_delegated=min_delegated, stop_list=stop_list)
        return self.multisend(payouts, coin=coin, payload=payload, include_commission=include_commission)

    def pay_by_shares(self, shares, to_be_payed, coin="BIP", payload='', include_commission=True):
        """
        Выплаты по пропорциям
        :param shares: dict
        :param to_be_payed: int/float сумма выплаты
        :param coin: str 'SYMBOL'
        :param payload: str
        :param include_commission: bool
        :return: node response
        """
        payouts = self._convert_shares_to_payouts(shares, to_be_payed)
        return self.multisend(payouts, coin=coin, payload=payload, include_commission=include_commission)

    def send(self, to, value, coin="BIP", payload='', include_commission=True):
        value = Decimal(str(value))

        nonce = API.get_nonce(self.address)
        tx = MinterSendCoinTx(coin=coin, to=to, value=value, nonce=nonce, gas_coin=coin, payload=payload)

        if include_commission:
            if coin == 'BIP':
                commission = to_bip(tx.get_fee())
            else:
                tx.sign(self.private_key)
                commission = API.estimate_tx_commission(tx.signed_tx, pip2bip=True)['result']['commission']

            tx.value = value - commission

        # Проверяем на ошибки
        if tx.value <= 0:
            print(f'Ошибка: Комиссия ({to_bip(tx.get_fee())}) превышает сумму выплаты ({value})')
            return
        elif tx.value > self.get_balance(in_bip=True)[coin]:
            print(f'Ошибка: На кошельке недостаточно {coin}')
            return

        tx.sign(private_key=self.private_key)
        return API.send_transaction(tx.signed_tx)

    def multisend(self, to_dict, coin="BIP", payload='', include_commission=True):
        """
        Multisend на любое количество адресов

        :param to_dict: dict {address: value, ...}
        :param coin: str 'SYMBOL'
        :param payload: str 'Комментарий к транзакции'
        :param include_commission: bool Платит с учетом комиссии
        :return:
        """

        # Генерация общего списка транзакций и расчет общей суммы выплаты
        all_txs = []
        total_value = 0
        for d_address, d_value in to_dict.items():
            d_value = Decimal(str(d_value))
            all_txs.append({'coin': coin, 'to': d_address, 'value': d_value})
            total_value += d_value

        # Проверяем хватит ли баланса для совершения транзакции
        balance = self.get_balance(in_bip=True)[coin]
        if total_value > balance:
            print(f'Ошибка: На кошельке недостаточно {coin}. Нужно {total_value}, а у нас {balance}')
            return

        # Разбивка на списки по 100 транзакций
        all_txs = self._split_txs(all_txs)

        # Генерируем шаблоны транзакций
        tx_templates = [MinterMultiSendCoinTx(txs, nonce=1, gas_coin=coin, payload=payload) for txs in all_txs]

        # Считаем общую комиссию за все транзакции
        if coin == 'BIP':
            total_commission = to_bip(sum(tx.get_fee() for tx in tx_templates))
        else:
            [tx.sign(self.private_key) for tx in tx_templates]
            total_commission = sum(API.estimate_tx_commission(tx.signed_tx, pip2bip=True)['result']['commission'] for tx in tx_templates)

        # Если перевод с учетом комиссии, то пересчитываем выплаты
        if include_commission:
            new_total_value = total_value - total_commission
            if new_total_value <= 0:
                print(f'Ошибка: Комиссия ({total_commission}) превышает сумму выплаты ({total_value})')
                return

            for tx in tx_templates:
                for tx_dict in tx.txs:
                    tx_dict['value'] = new_total_value * Decimal(str(tx_dict['value'])) / Decimal(str(total_value))
                    print(tx_dict['value'])
        else:
            total_value -= total_commission
            if total_value <= 0:
                print(f'Ошибка: Комиссия ({total_commission}) превышает сумму выплаты ({total_value})')
                return

        r_out = []
        # Делаем multisend
        for tx in tx_templates:
            tx.nonce = API.get_nonce(self.address)
            tx.sign(self.private_key)
            r = API.send_transaction(tx.signed_tx)
            r_out.append(r)
            if len(tx_templates) > 1:
                self._wait_for_nonce(tx.nonce)

        return r_out

    # ------------------------------------------
    # СЛУЖЕБНЫЕ ФУНКЦИИ
    # ------------------------------------------

    @staticmethod
    def _convert_shares_to_payouts(shares, to_be_payed):

        for key in shares:
            shares[key] = Decimal(str(shares[key])) * Decimal(str(to_be_payed))

        return shares

    @staticmethod
    def _split_txs(txs, length=100):
        """
        Делает несколько multisend списков по length транзакций на список
        """
        if length > 100:
            print('[!] Ошибка в Wallet._split_txs: Максимум 100 адресов на 1 multisend транзакцию')
            return

        txs_list = []

        while len(txs) > length:
            txs_list.append(txs[:length])
            txs = txs[length:]
        else:
            txs_list.append(txs)

        return txs_list

    def _wait_for_nonce(self, old_nonce):
        """
        Прерывается, если новый nonce != старый nonce
        """
        while True:
            nonce = API.get_nonce(self.address)
            if nonce != old_nonce:
                break

            time.sleep(1)

    @staticmethod
    def _only_symbol(balances, symbol):
        """
        True, если на балансе кошелька только symbol
        """
        if len(balances) > 1:
            return False
        elif symbol in balances:
            return True