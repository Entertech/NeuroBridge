//
// Created by Enter M1 on 2023/8/29.
//

#ifndef AFFECTIVECPP_PEPRHANDLER_H
#define AFFECTIVECPP_PEPRHANDLER_H
#include "TypeDefine.h"
#include "Data.h"
#include "Device.h"

namespace basic::dsp::peprhandler
{
    struct PEPRHandlerTemp
    {
        //心率相关
        vectord peDataBuffer; //原始压电数据缓存（用于计算脉搏波）
        double preHr; //上一心率值
        double preHrv; //上一心率变异性值
        vectord nnIntervalBuffer; //心率间期缓存
        vectord hrBuffer; //心率缓存
        vectord preBcgBoundary; //上一脉搏波输出波形边界
        vectord bcgAmpBuffer; //脉搏波幅值缓存（用于信号质量判断）
        vectord peRangeBuffer; //压电数据跨度缓存（用于信号质量判断）
        vectori bcgQualityBuffer; // 脉搏波信号质量缓存（用于更新滤波参数）
        double preBcgFl; //上一脉搏波滤波下限截止频率（用于自适应滤波）
        double preBcgFh; //上一脉搏波滤波上限截止频率（用于自适应滤波）

        //呼吸波相关
        vectord peDataBufferLong; //原始压电数据长缓存（用于计算呼吸波）
        double preRr; //上一呼吸率值
        vectord preRwLfBoundary; //上一呼吸波输出波形边界
        double rwHfDrift; //呼吸波快波漂移（用于数字滤波）
        vectord preInputRwHfFilterData; //上一输入呼吸波快波滤波数据（用于数字滤波）
        vectord preOutputRwHfFilterData; //上一输出呼吸波快波滤波数据（用于数字滤波）
        vectord preInputRwFilterData; //上一输入呼吸波滤波数据（用于数字滤波）
        vectord preOutputRwFilterData; //上一输出呼吸波滤波数据（用于数字滤波）
        double intervalDataDrift; //脉搏波间隔漂移（用于滤除心率脉搏波间隔漂移得到呼吸波，使呼吸波接近零轴）
        double preSyncCor; // 上一神经系统同步频率（用于判断呼吸波快波比例）
        vectord rwCalWaveBuffer; //用于计算呼吸率的呼吸波波形
        double rwHfRate; //呼吸波快波比例（用于合成呼吸波）
        vectord rwRangeBuffer; //呼吸波跨度缓存（用于信号质量判断）

        //特征
        int peakIndexShift; //峰值索引位移（用于计算全量峰值索引）
        vectori prePeakIndexList; //上一峰值索引序列
        vectori prePeakIndexRes; //上一峰值索引输出
        double preNnIntervalAvg; //上一心率间期平均值
        double preNnInterval; //上一心率间期
        vectord nnIntervalStatBuffer; //心率间期统计缓存（用于计算心率统计特征）
    };

    struct PEPRHandlerResult
    {
        double hr; //心率值
        double hrv; //心率变异性
        double rr; //呼吸率
        BCGQuality bcgQuality; //脉搏波信号质量
        RWQuality rwQuality; //呼吸波信号质量
        vectord bcgWave; //脉搏波波形
        vectord rwWave; //呼吸波波形
        vectord nnInterval; //NN间期
        HRPower hrPower; //心率频段能量
        double  syncCor; //神经系统同步频率相关系数
        vectori peakIndex; //峰值索引
        double nnIntervalFeatureAmo; //心率间期统计幅度
        double nnIntervalFeatureMo; //心率间期统计众数
        double nnIntervalFeatureMxdmn; //心率间期统计
    };

    void initTemp(PEPRHandlerTemp &tmp);


    /// \brief 压电压阻算法处理器
    /// \param peRawData 原始压电数据
    /// \param prRawData 原始压阻数据
    /// \param bcgSplitSec 脉搏波片段长度（单位：秒）
    /// \param rwSplitSec 呼吸波片段长度（单位：秒）
    /// \param bcgEpochSec 用于计算心率等参数的脉搏波波形部分长度（单位：秒）
    /// \param rwEpochSec 用于计算心率等参数的呼吸波波形部分长度（单位：秒）
    /// \param bcgInvalidSec 脉搏波尾部无效长度（避免滤波引起的尾部振荡导致结果不准，单位：秒）
    /// \param rwInvalidSec 呼吸波尾部无效长度（避免滤波引起的尾部振荡导致结果不准，单位：秒）
    /// \param deviceInfo 设备信息
    /// \param tmp 缓存
    /// \param scopeLim 计算心率变异性及频段能量所需的心率序列时长（单位：秒）
    /// \param rwHfRate 呼吸波快波比例（调节该值来平衡呼吸波快波与慢波的比例，None表示自适应）
    /// \param peSampleInterval 压电信号降采样使用的采样间隔
    /// \return 处理后的信号及其他结果，更新后的缓存
    PEPRHandlerResult handler(const vectori &peRawData, const vectori &prRawData, double bcgSplitSec, double rwSplitSec,
                              double bcgEpochSec, double rwEpochSec, double bcgInvalidSec, double rwInvalidSec,
                              DeviceInfo *deviceInfo, PEPRHandlerTemp &tmp, double scopeLim = 60., double rwHfRate = -1,
                              int peSampleInterval = 5);

}

#endif //AFFECTIVECPP_PEPRHANDLER_H
