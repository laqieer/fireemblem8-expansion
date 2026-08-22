#include "global.h"

#include <stdio.h>
#include <string.h>

#include "sio_core.h"

u32 gUnk_71;
u32 gUnk_72;
u32 gSioStateId;
struct SioMessage gSioMsgBuf;
u8 gUnk_75[SIO_MAX_PACKET];
u8 gGenericBuffer[0x2000];
struct LinkArenaStMaybe gLinkArenaSt;

static int sFailures;
static int sVerifyCalls;
static int sProcBreakCalls;
static int sProcEndCalls;

#define CHECK(condition) do { \
    if (!(condition)) { \
        printf("FAIL: %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        sFailures++; \
    } \
} while (0)

void Proc_Break(ProcPtr proc)
{
    (void)proc;
    sProcBreakCalls++;
}

void Proc_End(ProcPtr proc)
{
    (void)proc;
    sProcEndCalls++;
}

static bool RejectPayload(void * data)
{
    (void)data;
    sVerifyCalls++;
    return false;
}

static void ResetState(void)
{
    memset(gSioSt, 0, sizeof(*gSioSt));
    memset(gSioOutgoing, 0, sizeof(gSioOutgoing));
    memset(&gSioMsgBuf, 0, sizeof(gSioMsgBuf));
    gSioSt->selfId = 0;
    sVerifyCalls = 0;
    sProcBreakCalls = 0;
    sProcEndCalls = 0;
}

static void FillPacket(struct SioData * packet, u8 sender, u16 sequence, u16 len, u8 seed)
{
    int i;

    memset(packet, 0, sizeof(*packet));
    packet->head.kind = SIO_MSG_DATA;
    packet->head.sender = sender;
    packet->head.param = sequence;
    packet->len = len;

    for (i = 0; i < len && i < (int)SIO_MAX_DATA; i++)
        packet->bytes[i] = seed + i;
}

static void TestIngressValidation(void)
{
    struct SioData packet;

    FillPacket(&packet, 1, 0, 4, 0x10);
    CHECK(Sio_IsValidDataPacket(&packet, offsetof(struct SioData, bytes) + 4, 1));
    FillPacket(&packet, 1, 0, 3, 0x10);
    CHECK(!Sio_IsValidDataPacket(&packet, offsetof(struct SioData, bytes) + 3, 1));
    FillPacket(&packet, 1, 0, 0x28, 0x10);
    CHECK(Sio_IsValidDataPacket(&packet, offsetof(struct SioData, bytes) + 0x28, 1));
    FillPacket(&packet, 1, 0, SIO_MAX_DATA, 0x10);
    CHECK(Sio_IsValidDataPacket(&packet, SIO_MAX_PACKET, 1));
    FillPacket(&packet, 1, 0, 22, 0x10);
    CHECK(Sio_IsValidDataPacket(&packet, offsetof(struct SioData, bytes) + 22, 1));

    FillPacket(&packet, 1, 0, 4, 0x10);
    CHECK(!Sio_IsValidDataPacket(&packet, offsetof(struct SioData, bytes) + 3, 1));
    CHECK(!Sio_IsValidDataPacket(&packet, offsetof(struct SioData, bytes) + 5, 1));
    CHECK(!Sio_IsValidDataPacket(&packet, offsetof(struct SioData, bytes) + 4, 2));

    packet.head.sender = 4;
    CHECK(!Sio_IsValidDataPacket(&packet, offsetof(struct SioData, bytes) + 4, 4));

    packet.head.sender = 0xFF;
    CHECK(!Sio_IsValidDataPacket(&packet, offsetof(struct SioData, bytes) + 4, 0xFF));

    packet.head.sender = 1;
    packet.len = SIO_MAX_DATA + 1;
    CHECK(!Sio_IsValidDataPacket(&packet, SIO_MAX_PACKET, 1));
    packet.len = UINT16_MAX;
    CHECK(!Sio_IsValidDataPacket(&packet, SIO_MAX_PACKET, 1));
}

static void TestQueueAdmission(void)
{
    struct SioData packet;
    struct SioPending before;

    ResetState();
    FillPacket(&packet, 1, 0, 4, 0x20);
    CHECK(SioQueuePendingRecvData(&packet, offsetof(struct SioData, bytes) + 3) < 0);
    CHECK(gSioSt->nextPendingRecv == 0);
    CHECK(gSioSt->pendingRecv[0].packet.head.kind == 0);

    packet.len = SIO_MAX_DATA + 1;
    CHECK(SioQueuePendingRecvData(&packet, SIO_MAX_PACKET) < 0);
    CHECK(gSioSt->nextPendingRecv == 0);

    FillPacket(&packet, 4, 0, 4, 0x30);
    CHECK(SioQueuePendingRecvData(&packet, offsetof(struct SioData, bytes) + 4) < 0);
    CHECK(gSioSt->nextPendingRecv == 0);

    FillPacket(&packet, 1, 0, 4, 0x30);
    memset(&gSioSt->pendingRecv[0], 0x5A, sizeof(gSioSt->pendingRecv[0]));
    before = gSioSt->pendingRecv[0];
    CHECK(SioQueuePendingRecvData(&packet, offsetof(struct SioData, bytes) + 4) < 0);
    CHECK(memcmp(&before, &gSioSt->pendingRecv[0], sizeof(before)) == 0);
    CHECK(gSioSt->nextPendingRecv == 0);

    memset(&gSioSt->pendingRecv[0], 0, sizeof(gSioSt->pendingRecv[0]));
    CHECK(SioQueuePendingRecvData(&packet, offsetof(struct SioData, bytes) + 4) == 0);
    CHECK(gSioSt->nextPendingRecv == 1);
    CHECK(gSioSt->pendingRecv[0].packet.len == 4);
    CHECK(memcmp(gSioSt->pendingRecv[0].packet.bytes, packet.bytes, 4) == 0);
}

static void CheckValidReceive(u16 len)
{
    int i;
    int got;
    u8 sender = 0xA5;
    u8 destination[SIO_MAX_DATA + 2];
    struct SioData packet;

    ResetState();
    memset(destination, 0xA5, sizeof(destination));
    FillPacket(&packet, 1, 0, len, 0x40);
    CHECK(SioQueuePendingRecvData(
        &packet,
        offsetof(struct SioData, bytes) + len) == 0);

    got = SioReceiveData(&destination[1], len, &sender, NULL);
    CHECK(got == len);
    CHECK(sender == 1);
    CHECK(destination[0] == 0xA5);
    CHECK(destination[len + 1] == 0xA5);
    CHECK(gSioSt->seq[1] == 1);

    for (i = 0; i < len; i++)
        CHECK(destination[i + 1] == (u8)(0x40 + i));
}

static void TestReceiveCapacityAndVerification(void)
{
    int got;
    u8 sender = 0xA5;
    u8 destination[12];
    u8 before[sizeof(destination)];
    struct SioData packet;

    CheckValidReceive(4);
    CheckValidReceive(0x28);
    CheckValidReceive(SIO_MAX_DATA);

    ResetState();
    memset(destination, 0xA5, sizeof(destination));
    memcpy(before, destination, sizeof(before));
    FillPacket(&packet, 1, 0, 6, 0x50);
    CHECK(SioQueuePendingRecvData(&packet, offsetof(struct SioData, bytes) + 6) == 0);
    got = SioReceiveData(&destination[4], 4, &sender, RejectPayload);
    CHECK(got < 0);
    CHECK(memcmp(before, destination, sizeof(destination)) == 0);
    CHECK(sVerifyCalls == 0);
    CHECK(gSioSt->seq[1] == 0);
    CHECK(gSioSt->nextPendingRead == 1);
    CHECK(gSioOutgoing[0] == 0);

    ResetState();
    memset(destination, 0xA5, sizeof(destination));
    memcpy(before, destination, sizeof(before));
    FillPacket(&packet, 1, 0, 4, 0x60);
    CHECK(SioQueuePendingRecvData(&packet, offsetof(struct SioData, bytes) + 4) == 0);
    got = SioReceiveData(&destination[4], 4, &sender, RejectPayload);
    CHECK(got == 0);
    CHECK(memcmp(before, destination, sizeof(destination)) == 0);
    CHECK(sVerifyCalls == 1);
    CHECK(gSioSt->seq[1] == 0);

    ResetState();
    memset(destination, 0xA5, sizeof(destination));
    memcpy(before, destination, sizeof(before));
    FillPacket(&packet, 4, 0, 4, 0x70);
    gSioSt->pendingRecv[0].packet = packet;
    got = SioReceiveData(&destination[4], 4, &sender, RejectPayload);
    CHECK(got < 0);
    CHECK(memcmp(before, destination, sizeof(destination)) == 0);
    CHECK(sVerifyCalls == 0);
}

static void TestEmitBoundsAndQueueIntegrity(void)
{
    int i;
    int result;
    u8 payload[SIO_MAX_DATA + 1];
    struct SioSt before;

    for (i = 0; i < (int)sizeof(payload); i++)
        payload[i] = i;

    ResetState();
    result = SioEmitData(payload, SIO_MAX_DATA);
    CHECK(result == 0);
    CHECK(gSioSt->pendingSend[0].packet.len == SIO_MAX_DATA);
    CHECK(memcmp(gSioSt->pendingSend[0].packet.bytes, payload, SIO_MAX_DATA) == 0);

    ResetState();
    before = *gSioSt;
    CHECK(SioEmitData(payload, SIO_MAX_DATA + 1) < 0);
    CHECK(memcmp(&before, gSioSt, sizeof(before)) == 0);

    ResetState();
    before = *gSioSt;
    CHECK(SioEmitData(payload, 3) < 0);
    CHECK(memcmp(&before, gSioSt, sizeof(before)) == 0);

    ResetState();
    before = *gSioSt;
    CHECK(SioEmitData(NULL, 4) < 0);
    CHECK(memcmp(&before, gSioSt, sizeof(before)) == 0);

    ResetState();
    gSioSt->pendingSend[0].packet.head.kind = SIO_MSG_DATA;
    before = *gSioSt;
    CHECK(SioEmitData(payload, 4) < 0);
    CHECK(memcmp(&before, gSioSt, sizeof(before)) == 0);

    memset(gSioOutgoing, 0xA5, sizeof(gSioOutgoing));
    CHECK(SioSend(payload, SIO_MAX_PACKET + 1) < 0);
    CHECK(SioSend(payload, 3) < 0);
    CHECK(gSioOutgoing[0] == 0xA5A5);
}

static void QueueDirectPacket(u8 sender, u16 sequence, u16 len, u8 seed)
{
    FillPacket(
        &gSioSt->pendingRecv[gSioSt->nextPendingRead].packet,
        sender,
        sequence,
        len,
        seed);
}

static void TestBigTransferLayoutAndCopy(void)
{
    int block;
    int i;
    u8 blockCountHi;
    u8 blockCountLo;
    u8 lastBlockLen;
    u16 blockCount;
    u32 totalSize;
    struct SioBigSendProc sendProc;
    struct SioBigReceiveProc proc;
    u8 source[0xC00 + 2];
    u8 destination[0xC00 + 2];

    CHECK(SioGetBigTransferLayout(4, &blockCount, &lastBlockLen));
    CHECK(blockCount == 1);
    CHECK(lastBlockLen == 4);
    CHECK(SioGetBigTransferLayout(SIO_MAX_DATA, &blockCount, &lastBlockLen));
    CHECK(blockCount == 1);
    CHECK(lastBlockLen == SIO_MAX_DATA);
    CHECK(SioGetBigTransferLayout(0xC00, &blockCount, &lastBlockLen));
    CHECK(blockCount == 26);
    CHECK(lastBlockLen == 22);
    CHECK(!SioGetBigTransferLayout(0, &blockCount, &lastBlockLen));
    CHECK(!SioGetBigTransferLayout(3, &blockCount, &lastBlockLen));
    CHECK(SioValidateBigTransferLayout(blockCount, lastBlockLen, 0xC00, &totalSize));
    CHECK(totalSize == 0xC00);
    CHECK(!SioValidateBigTransferLayout(27, 22, 0xC00, &totalSize));
    CHECK(!SioValidateBigTransferLayout(1, SIO_MAX_DATA + 1, 0xC00, &totalSize));

    ResetState();
    gSioSt->unk_01F = SIO_BIG_TRANSFER_ACTIVE;
    Sio_ResetState();
    CHECK(GetSioBigTransferStatus() == SIO_BIG_TRANSFER_IDLE);

    ResetState();
    memset(&sendProc, 0, sizeof(sendProc));
    sendProc.blockCount = 1;
    sendProc.lastBlockLen = 4;
    SioBigSend_Init(&sendProc);
    CHECK(GetSioBigTransferStatus() == SIO_BIG_TRANSFER_ACTIVE);
    CHECK(gSioSt->pendingSend[0].packet.len == 4);

    ResetState();
    memset(&sendProc, 0, sizeof(sendProc));
    memset(source, 0xA5, sizeof(source));
    for (i = 0; i < 0xC00; i++)
        source[i + 1] = i;
    sendProc.data = &source[1];
    sendProc.blockCount = blockCount;
    sendProc.currentBlock = blockCount - 1;
    sendProc.lastBlockLen = lastBlockLen;
    SioBigSend_Loop(&sendProc);
    CHECK(sendProc.currentBlock == blockCount);
    CHECK(gSioSt->pendingSend[0].packet.len == lastBlockLen);
    CHECK(memcmp(
        gSioSt->pendingSend[0].packet.bytes,
        &source[1 + (blockCount - 1) * SIO_MAX_DATA],
        lastBlockLen) == 0);
    CHECK(source[0] == 0xA5);
    CHECK(source[0xC01] == 0xA5);

    ResetState();
    memset(&proc, 0, sizeof(proc));
    memset(destination, 0xA5, sizeof(destination));
    proc.data = &destination[1];
    proc.capacity = 0xC00;
    blockCountHi = blockCount >> 8;
    blockCountLo = blockCount;
    QueueDirectPacket(1, 0, 4, 0);
    gSioSt->pendingRecv[0].packet.bytes[0] = 0;
    gSioSt->pendingRecv[0].packet.bytes[1] = blockCountHi;
    gSioSt->pendingRecv[0].packet.bytes[2] = blockCountLo;
    gSioSt->pendingRecv[0].packet.bytes[3] = lastBlockLen;
    SioBigReceive_RecvHeader(&proc);
    CHECK(proc.blockCount == 26);
    CHECK(proc.lastBlockLen == 22);
    CHECK(proc.receivedSize == 0xC00);

    for (block = 0; block < proc.blockCount; block++)
    {
        int len = block == proc.blockCount - 1 ? proc.lastBlockLen : SIO_MAX_DATA;

        QueueDirectPacket(1, gSioSt->seq[1], len, block);
        SioBigReceive_Loop(&proc);
    }

    CHECK(GetSioBigTransferStatus() == SIO_BIG_TRANSFER_COMPLETE);
    CHECK(destination[0] == 0xA5);
    CHECK(destination[0xC01] == 0xA5);

    for (i = 0; i < 0xC00; i++)
        CHECK(destination[i + 1] == (u8)(i / SIO_MAX_DATA + i % SIO_MAX_DATA));

    ResetState();
    memset(&proc, 0, sizeof(proc));
    memset(destination, 0xA5, sizeof(destination));
    proc.data = &destination[1];
    proc.capacity = 4;
    proc.blockCount = 1;
    proc.lastBlockLen = 4;
    QueueDirectPacket(1, 0, 3, 0x33);
    SioBigReceive_Loop(&proc);
    CHECK(GetSioBigTransferStatus() == SIO_BIG_TRANSFER_ERROR);
    CHECK(sProcEndCalls == 1);
    CHECK(destination[0] == 0xA5);
    CHECK(destination[1] == 0xA5);
    CHECK(destination[4] == 0xA5);
    CHECK(destination[5] == 0xA5);

}

int main(void)
{
    TestIngressValidation();
    TestQueueAdmission();
    TestReceiveCapacityAndVerification();
    TestEmitBoundsAndQueueIntegrity();
    TestBigTransferLayoutAndCopy();

    if (sFailures == 0)
    {
        puts("sio_protocol_host_test: ok");
        return 0;
    }

    printf("%d failure(s)\n", sFailures);
    return 1;
}
