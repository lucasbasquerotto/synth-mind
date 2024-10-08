# pylint: disable=too-many-branches
from collections import abc
import time
import math
import typing
import torch
from torch import nn
from torch import optim
from auto_mind.supervised._action_data import (
    BaseResult, TestResult, TrainResult, ExecutionCursor, EarlyStopper,
    TrainBatchInfo, TrainParams, TestParams)
from auto_mind.supervised._action_handlers import AbortedException

I = typing.TypeVar("I", bound=abc.Sized)
O = typing.TypeVar("O")
T = typing.TypeVar('T')
TG = typing.TypeVar("TG", bound=abc.Sized)
M = typing.TypeVar("M", bound=nn.Module)
OT = typing.TypeVar("OT", bound=optim.Optimizer)
RV = typing.TypeVar("RV", bound=BaseResult)
MT = typing.TypeVar("MT")

S = typing.TypeVar("S", bound=BaseResult)

class BatchHandlerRunParams(typing.Generic[I]):
    def __init__(self, data: I, batch: int, amount: int):
        self.data = data
        self.batch = batch
        self.amount = amount

class BatchHandlerData(typing.Generic[I, O, TG]):
    def __init__(
        self,
        amount: int,
        loss: float,
        accuracy: float | None,
        input: I,
        output: O,
        target: TG,
    ):
        self.amount = amount
        self.loss = loss
        self.accuracy = accuracy
        self.input = input
        self.output = output
        self.target = target

class MetricsHandlerInput(BatchHandlerData[I, O, TG], typing.Generic[I, O, TG]):
    def __init__(self, out: BatchHandlerData[I, O, TG], time_diff: int):
        super().__init__(
            amount=out.amount,
            loss=out.loss,
            accuracy=out.accuracy,
            input=out.input,
            output=out.output,
            target=out.target)

        self.time_diff = time_diff

class MetricsHandler(typing.Generic[I, O, TG, MT]):
    def define(self, data: MetricsHandlerInput[I, O, TG]) -> MT:
        raise NotImplementedError

    def add(self, current: MT | None, metrics: MT) -> MT:
        raise NotImplementedError

class TensorMetricsHandler(
    MetricsHandler[torch.Tensor, torch.Tensor, torch.Tensor, MT],
    typing.Generic[MT],
):
    def define(self, data: MetricsHandlerInput[torch.Tensor, torch.Tensor, torch.Tensor]) -> MT:
        raise NotImplementedError

    def add(self, current: MT | None, metrics: MT) -> MT:
        raise NotImplementedError

class BatchHandlerResult:
    def __init__(
        self,
        total_loss: float,
        total_accuracy: float | None,
        total_time: int,
        total_metrics: typing.Any | None,
    ):
        self.total_loss = total_loss
        self.total_accuracy = total_accuracy
        self.total_time = total_time
        self.total_metrics = total_metrics

class BatchHandler():
    """
    Base class for handling batch operations during an epoch.

    This class is responsible for updating batch metrics and states after each batch
    is processed during an epoch. It provides the necessary methods to manage and
    track the progress and results of batch operations.

    Attributes:
        amount: The total amount of data processed (for each item in each processed batch).
        total_loss: The total loss of the processed data (per item).
        total_accuracy: The total accuracy of the processed data (mean of the accuracies).
        total_time: The total time spent processing the data (in milliseconds, per batch).
        total_metrics: The total metrics of the processed data (processed per batch, customized).
        best_accuracy: The best accuracy achieved during the entire run.

    Methods:
        verify_early_stop() -> None:
            Raises an AbortedException if the run should be aborted.
        skip() -> bool:
            Returns True if the batch should be skipped (if it was already processed),
            False otherwise.
        run() -> None:
            Processes the data from a batch, updating the internal state with the new data.
    """
    def __init__(
        self,
        cursor: ExecutionCursor | None,
        metrics_handler: MetricsHandler[typing.Any, typing.Any, typing.Any, typing.Any] | None,
        best_accuracy: float | None,
    ):
        self.amount = cursor.amount if cursor else 0
        self.total_loss = cursor.total_loss if cursor else 0.0
        self.total_accuracy = cursor.total_accuracy if cursor else 0.0
        self.total_time: int = cursor.total_time if cursor else 0
        self.total_metrics = cursor.total_metrics if cursor else None
        self.best_accuracy = best_accuracy

        self._metrics_handler = metrics_handler

    def verify_early_stop(self) -> None:
        """
        Raises an AbortedException if the run should be aborted.
        """
        return

    def skip(self, batch: int) -> bool:
        raise NotImplementedError()

    def run(
        self,
        batch: int,
        total_batch: int | None,
        amount: int,
        last: bool,
        loss: float,
        accuracy: float | None,
        time_diff: int,
        batch_metrics: typing.Any | None,
    ) -> None:
        raise NotImplementedError()

    def _handle_main(
        self,
        amount: int,
        loss: float,
        accuracy: float | None,
        time_diff: int,
        batch_metrics: typing.Any | None,
    ) -> None:
        self.amount += amount
        self.total_loss += loss

        if accuracy is not None and self.total_accuracy is not None:
            self.total_accuracy += accuracy * amount
        else:
            self.total_accuracy = None

        self.total_time += time_diff

        if self._metrics_handler and batch_metrics:
            self.total_metrics = self._metrics_handler.add(self.total_metrics, batch_metrics)

class GeneralBatchHandlerParams:
    def __init__(
        self,
        save_every: int | None = None,
        print_every: int | None = None,
        metric_every: int | None = None,
    ):
        self.save_every = save_every
        self.print_every = print_every
        self.metric_every = metric_every

class GeneralBatchHandlerResults:
    def __init__(
        self,
        batch: int,
        total_batch: int | None,
        cursor: ExecutionCursor | None,
        last_epoch_accuracies: list[tuple[int, float]] | None,
        last_epoch_losses: list[tuple[int, float]] | None,
        last_epoch_times: list[tuple[int, int]] | None,
        last_epoch_metrics: list[tuple[int, typing.Any]] | None,
    ):
        self.batch = batch
        self.total_batch = total_batch
        self.cursor = cursor
        self.last_epoch_accuracies = last_epoch_accuracies
        self.last_epoch_losses = last_epoch_losses
        self.last_epoch_times = last_epoch_times
        self.last_epoch_metrics = last_epoch_metrics

class GeneralBatchHandler(BatchHandler):
    def __init__(
        self,
        params: GeneralBatchHandlerParams,
        get_results: typing.Callable[[], GeneralBatchHandlerResults],
        update_results: typing.Callable[[GeneralBatchHandlerResults], None],
        print_prefix: str,
        get_batch_info: typing.Callable[[TrainBatchInfo[typing.Any]], str | None],
        save_state: typing.Callable[[], None],
        metrics_handler: MetricsHandler[typing.Any, typing.Any, typing.Any, typing.Any] | None,
        early_stopper: EarlyStopper | None = None,
        validation: bool = False,
        test: bool = False,
        best_accuracy: float | None = None,
    ):
        results = get_results()
        cursor = results.cursor

        super().__init__(
            cursor=cursor,
            metrics_handler=metrics_handler,
            best_accuracy=best_accuracy)

        self._params = params
        self._get_results = get_results
        self._update_results = update_results
        self._print_prefix = print_prefix
        self._save_state = save_state
        self._early_stopper = early_stopper
        self._get_batch_info = get_batch_info
        self._validation = validation
        self._test = test

        self._print_loss: float = 0
        self._print_accuracy: float | None = 0
        self._print_count = 0
        self._print_metrics: typing.Any | None = None

        self._metric_loss: float = 0
        self._metric_accuracy: float | None = 0
        self._metric_count = 0
        self._metric_time = 0
        self._metrics: typing.Any | None = None

        self._start = time.time()

    def verify_early_stop(self) -> None:
        if not self._early_stopper:
            return

        if self._early_stopper.check():
            raise AbortedException()

    def skip(self, batch: int) -> bool:
        results = self._get_results()
        start_batch = results.batch + 1
        return batch < start_batch

    def run(
        self,
        batch: int,
        total_batch: int | None,
        amount: int,
        last: bool,
        loss: float,
        accuracy: float | None,
        time_diff: int,
        batch_metrics: typing.Any | None,
    ) -> None:
        if self.skip(batch):
            return

        super()._handle_main(
            amount=amount,
            loss=loss,
            accuracy=accuracy,
            time_diff=time_diff,
            batch_metrics=batch_metrics,
        )

        params = self._params
        results = self._get_results()
        update_results = self._update_results
        print_prefix = self._print_prefix
        save_state = self._save_state
        metrics_handler = self._metrics_handler

        self._print_loss += loss / amount
        if accuracy is not None and self._print_accuracy is not None:
            self._print_accuracy += accuracy
        else:
            self._print_accuracy = None
        self._print_count += 1

        print_loss = self._print_loss
        print_accuracy = self._print_accuracy
        print_count = self._print_count

        self._metric_loss += loss / amount
        if accuracy is not None and self._metric_accuracy is not None:
            self._metric_accuracy += accuracy
        else:
            self._metric_accuracy = None
        self._metric_time += time_diff
        self._metric_count += 1

        metric_loss = self._metric_loss
        metric_accuracy = self._metric_accuracy
        metric_time = self._metric_time
        metric_count = self._metric_count

        if metrics_handler:
            self._print_metrics = metrics_handler.add(self._print_metrics, batch_metrics)
            self._metrics = metrics_handler.add(self._metrics, batch_metrics)
        else:
            self._print_metrics = None
            self._metrics = None

        metrics = self._metrics
        print_metrics = self._print_metrics

        start = self._start

        save_every = params.save_every
        print_every = params.print_every
        metric_every = params.metric_every

        # print every print_every batches, but not on the last batch (it should be printed outside)
        do_print = (print_every is not None) and (last or (batch % print_every == 0))
        # save every save_every batches, but not on the last batch (it should be saved outside)
        do_save = (save_every is not None) and (last or (batch % save_every == 0))
        # update the persistent result, which is done when going to save,
        # and also when defining the metrics
        do_update = do_save or last or (metric_every is None) or (batch % metric_every == 0)
        # change metric values when updating, as long as metric_every is set
        do_metric = do_update and (metric_every is not None)

        if do_print:
            info: TrainBatchInfo[typing.Any] = TrainBatchInfo(
                loss=print_loss,
                accuracy=print_accuracy,
                metrics=print_metrics,
                count=print_count,
                batch=batch,
                total_batch=total_batch,
                first=batch <= print_every if print_every is not None else False,
                last=last,
                start=start,
                prefix=print_prefix,
                validation=self._validation,
                test=self._test)
            info_str = self._get_batch_info(info)

            if info_str:
                print(info_str)

            self._print_count = 0
            self._print_loss = 0
            self._print_accuracy = 0
            self._print_metrics = None

        if do_update:
            results.batch = batch
            results.total_batch = total_batch
            results.cursor = ExecutionCursor(
                amount=self.amount,
                total_loss=self.total_loss,
                total_accuracy=self.total_accuracy,
                total_metrics=self.total_metrics,
                total_time=self.total_time)

            if do_metric:
                accuracies = results.last_epoch_accuracies or []
                losses = results.last_epoch_losses or []
                times = results.last_epoch_times or []
                epoch_metrics = results.last_epoch_metrics or []

                if metric_accuracy is not None:
                    accuracies.append((batch, metric_accuracy / metric_count))
                losses.append((batch, metric_loss / metric_count))
                times.append((batch, metric_time))
                epoch_metrics.append((batch, metrics))

                results.last_epoch_accuracies = accuracies
                results.last_epoch_losses = losses
                results.last_epoch_times = times
                results.last_epoch_metrics = epoch_metrics

            update_results(results)

            self._metric_count = 0
            self._metric_loss = 0
            self._metric_accuracy = 0
            self._metrics = None

            if do_save:
                save_state()

class TrainBatchHandler(GeneralBatchHandler, typing.Generic[I, O, TG, MT]):
    def __init__(
        self,
        validation: bool,
        epoch: int,
        params: TrainParams[I, O, TG, MT],
        results: TrainResult,
        get_batch_info: typing.Callable[[TrainBatchInfo[typing.Any]], str | None],
        save_state: typing.Callable[[TrainResult], None],
        metrics_handler: MetricsHandler[typing.Any, typing.Any, typing.Any, typing.Any] | None,
        early_stopper: EarlyStopper | None = None,
    ):
        def get_results() -> GeneralBatchHandlerResults:
            batch = (
                results.val_batch
                if validation
                else results.train_batch)

            total_batch = (
                results.val_total_batch
                if validation
                else results.train_total_batch)

            cursor = (
                results.batch_val_cursor
                if validation
                else results.batch_train_cursor)

            accuracies = (
                results.last_epoch_val_accuracies
                if validation
                else results.last_epoch_accuracies)

            losses = (
                results.last_epoch_val_losses
                if validation
                else results.last_epoch_losses)

            times = (
                results.last_epoch_val_times
                if validation
                else results.last_epoch_times)

            metrics = (
                results.last_epoch_val_metrics
                if validation
                else results.last_epoch_metrics)

            return GeneralBatchHandlerResults(
                batch=batch,
                total_batch=total_batch,
                cursor=cursor,
                last_epoch_accuracies=accuracies,
                last_epoch_losses=losses,
                last_epoch_times=times,
                last_epoch_metrics=metrics)

        def update_results(main_result: GeneralBatchHandlerResults) -> None:
            accuracies = main_result.last_epoch_accuracies
            losses = main_result.last_epoch_losses
            times = main_result.last_epoch_times
            metrics = main_result.last_epoch_metrics

            if validation:
                results.val_batch = main_result.batch
                results.val_total_batch = main_result.total_batch
                results.batch_val_cursor = main_result.cursor
                results.last_epoch_val_accuracies = accuracies
                results.last_epoch_val_losses = losses
                results.last_epoch_val_times = times
                results.last_epoch_val_metrics = metrics
            else:
                results.train_batch = main_result.batch
                results.train_total_batch = main_result.total_batch
                results.batch_train_cursor = main_result.cursor
                results.last_epoch_accuracies = accuracies
                results.last_epoch_losses = losses
                results.last_epoch_times = times
                results.last_epoch_metrics = metrics

        epochs = params.epochs
        epoch_cap = math.ceil(math.log10(epoch))
        epoch_str = f'{epoch:>{epoch_cap}} ({100.0 * epoch / epochs:>5.1f}%)'
        type_str = 'validation' if validation else 'train'
        print_prefix = f'> [{type_str}] [epoch {epoch_str}] '

        super().__init__(
            params=GeneralBatchHandlerParams(
                save_every=params.save_every,
                print_every=params.print_every,
                metric_every=params.metric_every),
            get_results=get_results,
            update_results=update_results,
            print_prefix=print_prefix,
            get_batch_info=get_batch_info,
            save_state=lambda: save_state(results),
            early_stopper=early_stopper,
            metrics_handler=metrics_handler,
            validation=validation,
            best_accuracy=results.best_accuracy if results else None)

        self._active = params.batch_interval
        self.best_accuracy = results.best_accuracy if results else None

    def verify_early_stop(self) -> None:
        if self._active:
            super().verify_early_stop()

    def skip(self, batch: int) -> bool:
        if self._active:
            return super().skip(batch)
        return False

    def run(
        self,
        batch: int,
        total_batch: int | None,
        amount: int,
        last: bool,
        loss: float,
        accuracy: float | None,
        time_diff: int,
        batch_metrics: typing.Any | None,
    ) -> None:
        if self._active:
            super().run(
                batch=batch,
                total_batch=total_batch,
                amount=amount,
                last=last,
                loss=loss,
                accuracy=accuracy,
                time_diff=time_diff,
                batch_metrics=batch_metrics)
        else:
            super()._handle_main(
                amount=amount,
                loss=loss,
                accuracy=accuracy,
                time_diff=time_diff,
                batch_metrics=batch_metrics)

class TestBatchHandler(GeneralBatchHandler, typing.Generic[I, O, TG, MT]):
    def __init__(
        self,
        params: TestParams[I, O, TG, MT],
        results: TestResult,
        get_batch_info: typing.Callable[[TrainBatchInfo[typing.Any]], str],
        save_state: typing.Callable[[TestResult], None],
        metrics_handler: MetricsHandler[typing.Any, typing.Any, typing.Any, typing.Any] | None,
        early_stopper: EarlyStopper | None = None,
    ):
        def get_results() -> GeneralBatchHandlerResults:
            return GeneralBatchHandlerResults(
                batch=results.batch,
                total_batch=results.total_batch,
                cursor=results.batch_cursor,
                last_epoch_accuracies=results.last_epoch_accuracies,
                last_epoch_losses=results.last_epoch_losses,
                last_epoch_times=results.last_epoch_times,
                last_epoch_metrics=results.last_epoch_metrics)

        def update_results(main_result: GeneralBatchHandlerResults) -> None:
            nonlocal results
            results.batch = main_result.batch
            results.total_batch = main_result.total_batch
            results.batch_cursor = main_result.cursor
            results.last_epoch_accuracies = main_result.last_epoch_accuracies
            results.last_epoch_losses = main_result.last_epoch_losses
            results.last_epoch_times = main_result.last_epoch_times

        super().__init__(
            params=GeneralBatchHandlerParams(
                save_every=params.save_every,
                print_every=params.print_every,
                metric_every=params.metric_every),
            get_results=get_results,
            update_results=update_results,
            print_prefix='[test] ',
            get_batch_info=get_batch_info,
            save_state=lambda: save_state(results),
            early_stopper=early_stopper,
            metrics_handler=metrics_handler,
            test=True)
