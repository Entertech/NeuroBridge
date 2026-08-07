#ifndef AC60C5AC_EA89_474A_A3DF_D28C2E0310AE
#define AC60C5AC_EA89_474A_A3DF_D28C2E0310AE
#include "Device.h"
#include "Data.h"
#include "NumCpp.hpp"
#include "TypeDefine.h"
#include <vector>

namespace basic
{
    namespace dsp
    {
        /**
         * @brief 脑电漂移去除
         * 
         * @param inputWave 脑电片段
         * @return vectord 滤除直流后的脑波片段
         */
        vectord eegDriftFilter(vectord inputWave);

        /**
         * @brief 脑电工频陷波器
         * 
         * @param inputWave 脑电片段
         * @return vectord 
         */
        vectord eegPfNotch(vectord &inputWave);

        /**
         * @brief 脑电低通滤波器
         * 
         * @param inputWave 脑电片段
         * @return vectord 
         */
        vectord eegLowpassFilter(vectord &inputWave);

        /**
         * @brief 脑电高通滤波器
         * 
         * @param inputWave 脑电片段
         * @return vectord 
         */
        vectord eegHighpassFilter(vectord &inputWave);

        /**
         * @brief 脑电伪迹去除
         * 
         * @param inputWave 原始脑电片段
         * @param waveletName 小波基名称
         * @return std::pair<vectord, vectord> 去除伪迹的脑电片段，伪迹
         */
        std::pair<vectord, vectord> eegArtifactRemove(vectord &inputWave, const std::string &waveletName = "sym5");

        /**
         * @brief 脑电小波去噪
         * 
         * @param inputWave 脑波片段
         * @param waveletName 小波基名称
         * @param thresholdMode 阈值模式（soft/hard）
         * @return vectord 小波去噪得到的脑波片段
         */
        vectord eegWaveletDenoise(vectord &inputWave,
                                  const std::string &waveletName = "db4",
                                  const std::string &thresholdMode = "soft");

        /**
         * @brief 脑电节律能量计算
         * 
         * @param inputWave 脑电片段
         * @param deviceInfo 设备信息
         * @return EEGPower 脑电节律能量
         */
        EEGPower eegPowerCal(vectord &inputWave, DeviceInfo *deviceInfo);

        /**
         * @brief 计算脑电信噪比
         * 
         * @param inputWave 滤波后信号
         * @param deviceInfo 设备信息
         * @return double 
         */
        double eegSnrCal(vectord &inputWave, DeviceInfo *deviceInfo);

        /**
         * @brief 脑电信号质量分级
         * 
         * @param filterWave 低通滤波后的脑电信号
         * @param deviceInfo 设备信息
         * @param waveAmplified 输入信号是否为运放放大后的幅值（默认采用未经还原处理的信号来判断信号质量）
         * @return EEGQuality 
         */
        EEGQuality eegQualityCal(vectord filterWave,
                                 DeviceInfo *deviceInfo,
                                 bool waveAmplified = true);
    }
}


#endif /* AC60C5AC_EA89_474A_A3DF_D28C2E0310AE */
