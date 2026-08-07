//
// Created by Enter M1 on 2023/9/12.
//

#ifndef AFFECTIVECPP_WAVELETTOOL_H
#define AFFECTIVECPP_WAVELETTOOL_H

#include "vector"

namespace basic::mathtool::wavelet
{
    using std::vector;
    struct C_L
    {
        vector<double> C;
        vector<int> L;
    };

    // С���˲���Low��ͨ��High��ͨ
    struct WaveFilter
    {
        vector<double> Low;
        vector<double> High;
    };

    // С���ֽ��ع���Ϣ��app���ƣ�detϸ��
    struct WaveCoeff
    {
        vector<double> app;
        vector<double> det;
    };

    // sym4С���˲���
    const double sym5_Lo_D[] = {0.027333068345077982,
                                0.029519490925774643,
                                -0.039134249302383094,
                                0.1993975339773936,
                                0.7234076904024206,
                                0.6339789634582119,
                                0.01660210576452232,
                                -0.17532808990845047,
                                -0.021101834024758855,
                                0.019538882735286728};
    const double sym5_Hi_D[] = {-0.019538882735286728,
                                -0.021101834024758855,
                                0.17532808990845047,
                                0.01660210576452232,
                                -0.6339789634582119,
                                0.7234076904024206,
                                -0.1993975339773936,
                                -0.039134249302383094,
                                -0.029519490925774643,
                                0.027333068345077982};
    const double sym5_Lo_R[] = {0.019538882735286728,
                                -0.021101834024758855,
                                -0.17532808990845047,
                                0.01660210576452232,
                                0.6339789634582119,
                                0.7234076904024206,
                                0.1993975339773936,
                                -0.039134249302383094,
                                0.029519490925774643,
                                0.027333068345077982};
    const double sym5_Hi_R[] = {0.027333068345077982,
                                -0.029519490925774643,
                                -0.039134249302383094,
                                -0.1993975339773936,
                                0.7234076904024206,
                                -0.6339789634582119,
                                0.01660210576452232,
                                0.17532808990845047,
                                -0.021101834024758855,
                                -0.019538882735286728};
    const static WaveFilter sym5_d = {vector<double>(sym5_Lo_D, sym5_Lo_D + 10), vector<double>(sym5_Hi_D, sym5_Hi_D + 10)};
    const static WaveFilter sym5_r = {vector<double>(sym5_Lo_R, sym5_Lo_R + 10), vector<double>(sym5_Hi_R, sym5_Hi_R + 10)};

    // db4С���˲���
    const double db4_Lo_D[] = {-0.010597401785069032,
                               0.0328830116668852,
                               0.030841381835560764,
                               -0.18703481171909309,
                               -0.027983769416859854,
                               0.6308807679298589,
                               0.7148465705529157,
                               0.2303778133088965};
    const double db4_Hi_D[] = {-0.2303778133088965,
                               0.7148465705529157,
                               -0.6308807679298589,
                               -0.027983769416859854,
                               0.18703481171909309,
                               0.030841381835560764,
                               -0.0328830116668852,
                               -0.010597401785069032};
    const double db4_Lo_R[] = {0.2303778133088965,
                               0.7148465705529157,
                               0.6308807679298589,
                               -0.027983769416859854,
                               -0.18703481171909309,
                               0.030841381835560764,
                               0.0328830116668852,
                               -0.010597401785069032};
    const double db4_Hi_R[] = {-0.010597401785069032,
                               -0.0328830116668852,
                               0.030841381835560764,
                               0.18703481171909309,
                               -0.027983769416859854,
                               -0.6308807679298589,
                               0.7148465705529157,
                               -0.2303778133088965};
    const static WaveFilter db4_d = {vector<double>(db4_Lo_D, db4_Lo_D + 8), vector<double>(db4_Hi_D, db4_Hi_D + 8)};
    const static WaveFilter db4_r = {vector<double>(db4_Lo_R, db4_Lo_R + 8), vector<double>(db4_Hi_R, db4_Hi_R + 8)};

    const WaveFilter& WFilters(
            const char* strWaveName, // С����
            const char d_or_r // �ֽ���ع�
    );

    // С����ֽ�
    C_L WaveDec(
            const vector<double>& signal, // �����ź�
            const int nMaxLevel, // �ֽ⼶��
            const char* strWaveName // ʹ��С��������
    );

    // ��ɢС���仯
    WaveCoeff DWT(
            const vector<double>& signal, // �����ź�
            const vector<double>& Lo_D, // �ֽ��ͨ�˲���
            const vector<double>& Hi_D // �ֽ��ͨ�˲���
    );

    // ͨ��һάС��ϵ���ع�����֧�ź�
    vector<double> WRCoef(
            const char a_or_d, // �ع�ϸ�ڻ�����ź�
            const vector<double>& C, // �зֽ�õ���С��ϸ�ںͽ���ϵ��
            const vector<int>& L, // ����ϵ���ĳ���
            const char* strWaveName, // �ع�С��������
            const int nLevel // �ع�����
    );

    // һά����ϵ��
    vector<double> AppCoef(
            const vector<double>& C, // �зֽ�õ���С��ϸ�ںͽ���ϵ��
            const vector<int>& L, // ����ϵ���ĳ���
            const char* strWaveName, // �ع�С��������
            const int nLevel // �ع�����
    );

    // һάϸ��ϵ��
    vector<double> DetCoef(
            const vector<double>& C, // �зֽ�õ���С��ϸ�ںͽ���ϵ��
            const vector<int>& L, // ����ϵ���ĳ���
            const int nLevel // �ع�����
    );

    // �ϲ�����2��ֵ������
    vector<double> UpsConv1(
            const vector<double>& signal,
            const vector<double>& filter,
            const int nLen,
            const char* strMode = "sym"
    );

    // ����
    vector<double> Conv(
            const vector<double>& vecSignal, // �ź�
            const vector<double>& vecFilter // �˲���
    );

    // ��һάС���任
    vector<double> IDWT(
            const vector<double>& app, // ����ϵ��
            const vector<double>& det, // ϸ��ϵ��
            const vector<double>& Lo_R, // �ع���ͨ�˲���
            const vector<double>& Hi_R, // �ع���ͨ�˲���
            const int nLenCentral
    );

    // ��չ����
    vector<double> WExtend(
            const vector<double>& signal, // �ź�
            const int nLenExt,
            const char* mode = "sym"
    );
    // ����
    vector<double> WConv1(
            const vector<double>& signal,
            const vector<double>& filter,
            const char* shape = "valid"
    );

    C_L WaveDec(const std::vector<double>& signal,  const int nMaxLevel, const char* strWaveName);
    std::vector<double> WRCoef(const char a_or_d, const std::vector<double>& C, const std::vector<int>& L,
                               const char* strWaveName, const int nLevel);
}

#endif //AFFECTIVECPP_WAVELETTOOL_H
