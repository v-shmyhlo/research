import os

import click
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.utils.data
import torchvision
import torchvision.transforms as T
from all_the_tools.config import load_config
from all_the_tools.metrics import Last, Mean
from all_the_tools.torch.utils import Saver
from tensorboardX import SummaryWriter
from tqdm import tqdm

from tacotron.dataset import LJ
from tacotron.model import Model
from tacotron.utils import collate_fn
from tacotron.utils import griffin_lim
from tacotron.vocab import CharVocab
from transforms import ApplyTo, ToTorch, Extract
from transforms.audio import LoadAudio
from transforms.text import VocabEncode
from utils import WarmupCosineAnnealingLR
from utils import compute_nrow

DEVICE = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')


@click.command()
@click.option('--config-path', type=click.Path(), required=True)
@click.option('--dataset-path', type=click.Path(), required=True)
@click.option('--experiment-path', type=click.Path(), required=True)
@click.option('--restore-path', type=click.Path())
@click.option('--workers', type=click.INT, default=os.cpu_count())
def main(config_path, **kwargs):
    config = load_config(
        config_path,
        **kwargs)

    vocab = CharVocab()
    train_transform, eval_transform = build_transforms(vocab, config)

    train_data_loader = torch.utils.data.DataLoader(
        LJ(config.dataset_path, transform=train_transform),
        batch_size=config.train.batch_size,
        shuffle=True,
        num_workers=config.workers,
        collate_fn=collate_fn,
        drop_last=True)
    # eval_data_loader = torch.utils.data.DataLoader(
    #     torchvision.datasets.CIFAR10(config.dataset_path, train=False, transform=eval_transform),
    #     batch_size=config.eval.batch_size,
    #     num_workers=config.workers)

    model = Model(config.model, vocab_size=len(vocab), sample_rate=config.sample_rate).to(DEVICE)
    model.apply(weights_init)
    optimizer = build_optimizer(model.parameters(), config)
    scheduler = build_scheduler(optimizer, config, len(train_data_loader))
    saver = Saver({
        'model': model,
        'optimizer': optimizer,
        'scheduler': scheduler,
    })
    if config.restore_path is not None:
        saver.load(config.restore_path, keys=['model'])

    for epoch in range(1, config.epochs + 1):
        train_epoch(model, train_data_loader, optimizer, scheduler, epoch=epoch, config=config)
        # eval_epoch(model, eval_data_loader, epoch=epoch, config=config)
        saver.save(
            os.path.join(config.experiment_path, 'checkpoint_{}.pth'.format(epoch)),
            epoch=epoch)


def build_optimizer(parameters, config):
    if config.train.opt.type == 'adam':
        optimizer = torch.optim.Adam(
            parameters,
            config.train.opt.lr,
            betas=config.train.opt.beta,
            eps=config.train.opt.eps,
            weight_decay=config.train.opt.weight_decay)
    else:
        raise AssertionError('invalid optimizer {}'.format(config.train.opt.type))

    return optimizer


def build_scheduler(optimizer, config, steps_per_epoch):
    if config.train.sched.type == 'multistep':
        scheduler = torch.optim.lr_scheduler.MultiStepLR(
            optimizer,
            [epoch * steps_per_epoch for epoch in config.train.sched.epochs],
            gamma=0.1)
    elif config.train.sched.type == 'warmup_cosine':
        scheduler = WarmupCosineAnnealingLR(
            optimizer,
            epoch_warmup=config.train.sched.epochs_warmup * steps_per_epoch,
            epoch_max=config.epochs * steps_per_epoch)
    else:
        raise AssertionError('invalid scheduler {}'.format(config.train.sched.type))

    return scheduler


def build_transforms(vocab, config):
    train_transform = eval_transform = T.Compose([
        ApplyTo('text', T.Compose([
            VocabEncode(vocab),
            ToTorch(),
        ])),
        ApplyTo('audio', T.Compose([
            LoadAudio(config.sample_rate),
            ToTorch(),
        ])),
        Extract(['text', 'audio'])
    ])

    return train_transform, eval_transform


def train_epoch(model, data_loader, optimizer, scheduler, epoch, config):
    metrics = {
        'loss': Mean(),
        'lr': Last(),
    }

    model.train()
    for (text, audio), (text_mask, audio_mask) in \
            tqdm(data_loader, desc='epoch {}/{}, train'.format(epoch, config.epochs)):
        text, audio, text_mask, audio_mask = \
            [x.to(DEVICE) for x in [text, audio, text_mask, audio_mask]]

        output, pre_output, target, target_mask, weight = model(text, audio, audio_mask)

        loss = mse(output, target, target_mask) + mse(pre_output, target, target_mask)

        metrics['loss'].update(loss.data.cpu().numpy())
        metrics['lr'].update(np.squeeze(scheduler.get_last_lr()))

        optimizer.zero_grad()
        loss.mean().backward()
        optimizer.step()
        scheduler.step()

    writer = SummaryWriter(os.path.join(config.experiment_path, 'train'))
    with torch.no_grad():
        gl_true = griffin_lim(target, model.spectra)
        gl_pred = griffin_lim(output, model.spectra)
        output, pre_output, target, weight = \
            [x.unsqueeze(1) for x in [output, pre_output, target, weight]]
        nrow = compute_nrow(target)

        for k in metrics:
            writer.add_scalar(k, metrics[k].compute_and_reset(), global_step=epoch)
        writer.add_image('target', torchvision.utils.make_grid(
            target, nrow=nrow, normalize=True), global_step=epoch)
        writer.add_image('output', torchvision.utils.make_grid(
            output, nrow=nrow, normalize=True), global_step=epoch)
        writer.add_image('pre_output', torchvision.utils.make_grid(
            pre_output, nrow=nrow, normalize=True), global_step=epoch)
        writer.add_image('weight', torchvision.utils.make_grid(
            weight, nrow=nrow, normalize=True), global_step=epoch)
        for i in tqdm(range(min(text.size(0), 4)), desc='writing audio'):
            writer.add_audio(
                'audio/{}'.format(i), audio[i], sample_rate=config.sample_rate, global_step=epoch)
            writer.add_audio(
                'griffin-lim-true/{}'.format(i), gl_true[i], sample_rate=config.sample_rate, global_step=epoch)
            writer.add_audio(
                'griffin-lim-pred/{}'.format(i), gl_pred[i], sample_rate=config.sample_rate, global_step=epoch)

    writer.flush()
    writer.close()


def eval_epoch(model, data_loader, epoch, config):
    metrics = {
        'loss': Mean(),
        'accuracy': Mean(),
    }

    with torch.no_grad():
        model.eval()
        for images, targets in tqdm(data_loader, desc='epoch {}/{}, eval'.format(epoch, config.epochs)):
            images, targets = images.to(DEVICE), targets.to(DEVICE)

            logits = model(images)
            loss = F.cross_entropy(input=logits, target=targets, reduction='none')

            metrics['loss'].update(loss.data.cpu().numpy())
            metrics['accuracy'].update((logits.argmax(-1) == targets).float().data.cpu().numpy())

    writer = SummaryWriter(os.path.join(config.experiment_path, 'eval'))
    with torch.no_grad():
        for k in metrics:
            writer.add_scalar(k, metrics[k].compute_and_reset(), global_step=epoch)
        writer.add_image('images', torchvision.utils.make_grid(
            images, nrow=compute_nrow(images), normalize=True), global_step=epoch)

    writer.flush()
    writer.close()


def weights_init(m):
    if isinstance(m, (nn.Conv2d,)):
        nn.init.kaiming_normal_(m.weight)
        if m.bias is not None:
            nn.init.zeros_(m.bias)
    elif isinstance(m, (nn.BatchNorm2d,)):
        nn.init.ones_(m.weight)
        nn.init.zeros_(m.bias)


def mse(input, target, mask):
    mask = mask.unsqueeze(1)

    loss = (input - target)**2
    loss = (loss * mask).sum(2).mean(1)

    return loss


if __name__ == '__main__':
    main()